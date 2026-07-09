use crate::session_log;
use crate::worker_contract::{
    JobProgress, JobResult, RunJobRequest, SubmitJobResponse, WorkerEnvelope, WorkerUiEvent,
    WORKER_EVENT_NAME,
};
use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Mutex, OnceLock};
use std::thread;
use tauri::{AppHandle, Emitter};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const WORKER_LANE_COUNT: usize = 2;

#[derive(Debug, Clone)]
struct PythonCandidate {
    label: String,
    program: String,
    prefix_args: Vec<String>,
}

struct QueuedJob {
    app: AppHandle,
    project_root: PathBuf,
    request: RunJobRequest,
}

struct WorkerRuntime {
    project_root: Option<PathBuf>,
    worker: Option<PersistentWorker>,
}

#[derive(Clone)]
struct WorkerLane {
    lane_id: usize,
    tx: mpsc::Sender<QueuedJob>,
    pending: Arc<AtomicUsize>,
}

struct WorkerQueues {
    lanes: Vec<WorkerLane>,
    next_lane: AtomicUsize,
}

struct PersistentWorker {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    stderr_rx: mpsc::Receiver<String>,
    stderr_buffer: String,
    candidate_label: String,
    lane_id: usize,
}

#[derive(Debug, Clone)]
struct ActiveJobControl {
    project_root: PathBuf,
    job_id: String,
}

static WORKER_QUEUES: OnceLock<WorkerQueues> = OnceLock::new();
static ACTIVE_JOBS: OnceLock<Mutex<Vec<Option<ActiveJobControl>>>> = OnceLock::new();

pub fn submit_job(
    app: AppHandle,
    project_root: &Path,
    request: RunJobRequest,
) -> Result<SubmitJobResponse> {
    if request.job_id.trim().is_empty() {
        bail!("job_id is empty");
    }
    if !request.source_path.exists() {
        bail!("source file does not exist: {}", request.source_path.display());
    }
    let output_md_path = request.output_dir.join(&request.output_file_name);
    if output_md_path.exists() {
        bail!("output file already exists: {}", output_md_path.display());
    }
    let asr_backend = request.asr_backend.trim().to_ascii_lowercase();
    let backend_label = if asr_backend == "cloud" {
        "云端 ASR"
    } else {
        "本地 ASR"
    };

    let lane = worker_queues().choose_lane();
    let queued_ahead = lane.pending.fetch_add(1, Ordering::SeqCst);
    let response = SubmitJobResponse {
        job_id: request.job_id.clone(),
        lane_id: lane.lane_id,
        queued_ahead,
    };

    emit_worker_event(
        &app,
        WorkerUiEvent {
            event: "queued".to_string(),
            job_id: request.job_id.clone(),
            lane_id: lane.lane_id,
            source_path: request.source_path.display().to_string(),
            stage: "queued".to_string(),
            progress: 0.0,
            detail: format!(
                "任务已进入{} 转写队列，分配到 Worker {}。",
                backend_label, lane.lane_id
            ),
            processed_ms: 0,
            total_ms: 0,
            payload: json!({ "queued_ahead": queued_ahead, "asr_backend": asr_backend }),
            result: None,
            error: None,
        },
    );

    if lane
        .tx
        .send(QueuedJob {
            app,
            project_root: project_root.to_path_buf(),
            request,
        })
        .is_err()
    {
        lane.pending.fetch_sub(1, Ordering::SeqCst);
        bail!("本地转写队列已停止，无法提交任务。");
    }

    Ok(response)
}

pub fn pause_lane(lane_id: usize) -> Result<()> {
    let active = active_job(lane_id)?;
    write_control_flag(&active, "control.pause")
}

pub fn resume_lane(lane_id: usize) -> Result<()> {
    let active = active_job(lane_id)?;
    remove_control_flag(&active, "control.pause")
}

pub fn terminate_lane(lane_id: usize) -> Result<()> {
    let active = active_job(lane_id)?;
    write_control_flag(&active, "control.cancel")
}

pub fn health_check(project_root: &Path) -> Result<Value> {
    let worker_dir = project_root.join("apps").join("worker-python");
    let mut last_error: Option<anyhow::Error> = None;

    for candidate in python_candidates(project_root) {
        let mut command = Command::new(&candidate.program);
        command
            .args(&candidate.prefix_args)
            .arg("-X")
            .arg("utf8")
            .arg("-m")
            .arg("app.main")
            .arg("--health-check")
            .current_dir(&worker_dir)
            .stderr(Stdio::piped())
            .stdout(Stdio::piped());
        command.env("PYTHONUTF8", "1");
        command.env("PYTHONIOENCODING", "utf-8");

        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);

        match command.output() {
            Ok(output) if output.status.success() => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let line = stdout
                    .lines()
                    .find(|line| !line.trim().is_empty())
                    .ok_or_else(|| anyhow!("health check returned empty stdout"))?;
                let envelope: WorkerEnvelope =
                    serde_json::from_str(line).context("failed to decode health check output")?;
                if envelope.kind == "health_check_ok" {
                    let mut payload = envelope.payload;
                    if let Some(object) = payload.as_object_mut() {
                        object.insert("python_candidate".to_string(), json!(candidate.label));
                    }
                    return Ok(payload);
                }
                last_error = Some(anyhow!("unexpected health check message: {}", envelope.kind));
            }
            Ok(output) => {
                let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                last_error = Some(anyhow!(
                    "candidate {} failed with status {}: {}",
                    candidate.label,
                    output.status,
                    stderr
                ));
            }
            Err(error) => {
                last_error = Some(anyhow!(
                    "failed to start candidate {}: {}",
                    candidate.label,
                    error
                ));
            }
        }
    }

    Err(last_error.unwrap_or_else(|| anyhow!("无法启动 Python worker。")))
}

impl WorkerQueues {
    fn choose_lane(&self) -> WorkerLane {
        let lane_count = self.lanes.len();
        let start = self.next_lane.fetch_add(1, Ordering::SeqCst) % lane_count;
        let min_pending = self
            .lanes
            .iter()
            .map(|lane| lane.pending.load(Ordering::SeqCst))
            .min()
            .unwrap_or(0);

        for offset in 0..lane_count {
            let lane = &self.lanes[(start + offset) % lane_count];
            if lane.pending.load(Ordering::SeqCst) == min_pending {
                return lane.clone();
            }
        }

        self.lanes[start].clone()
    }
}

fn worker_queues() -> &'static WorkerQueues {
    WORKER_QUEUES.get_or_init(|| {
        let mut lanes = Vec::with_capacity(WORKER_LANE_COUNT);
        for lane_id in 1..=WORKER_LANE_COUNT {
            let (tx, rx) = mpsc::channel();
            let pending = Arc::new(AtomicUsize::new(0));
            let pending_for_thread = pending.clone();
            thread::Builder::new()
                .name(format!("asr-tauri-worker-lane-{lane_id}"))
                .spawn(move || worker_queue_loop(lane_id, pending_for_thread, rx))
                .expect("failed to start ASR worker queue thread");
            lanes.push(WorkerLane {
                lane_id,
                tx,
                pending,
            });
        }

        WorkerQueues {
            lanes,
            next_lane: AtomicUsize::new(0),
        }
    })
}

fn worker_queue_loop(
    lane_id: usize,
    pending: Arc<AtomicUsize>,
    rx: mpsc::Receiver<QueuedJob>,
) {
    let mut runtime = WorkerRuntime {
        project_root: None,
        worker: None,
    };

    for job in rx {
        set_active_job(
            lane_id,
            Some(ActiveJobControl {
                project_root: job.project_root.clone(),
                job_id: job.request.job_id.clone(),
            }),
        );

        let request = job.request.clone();
        emit_progress(
            &job.app,
            &request,
            JobProgress {
                worker_lane: lane_id,
                stage: "worker_starting".to_string(),
                progress: 0.0,
                detail: format!(
                    "Worker {lane_id} 开始处理：{}。",
                    request.source_path.display()
                ),
                processed_ms: 0,
                total_ms: 0,
                payload: json!({}),
            },
        );

        let run_result = runtime.run_job(&job.project_root, &request, lane_id, |progress| {
            emit_progress(&job.app, &request, progress);
        });

        match run_result {
            Ok(mut result) => {
                result.worker_lane = lane_id;
                session_log::info(&format!(
                    "tauri worker job completed | lane={} | job_id={} | md_path={}",
                    lane_id,
                    request.job_id,
                    result.md_path.display()
                ));
                emit_worker_event(
                    &job.app,
                    WorkerUiEvent {
                        event: "completed".to_string(),
                        job_id: request.job_id.clone(),
                        lane_id,
                        source_path: request.source_path.display().to_string(),
                        stage: "completed".to_string(),
                        progress: 1.0,
                        detail: "任务已完成，Markdown 已写出。".to_string(),
                        processed_ms: result.total_ms,
                        total_ms: result.total_ms,
                        payload: json!({}),
                        result: Some(result),
                        error: None,
                    },
                );
            }
            Err(error) => {
                let error = format!("{error:#}");
                session_log::error(&format!(
                    "tauri worker job failed | lane={} | job_id={} | error={}",
                    lane_id, request.job_id, error
                ));
                emit_worker_event(
                    &job.app,
                    WorkerUiEvent {
                        event: "failed".to_string(),
                        job_id: request.job_id.clone(),
                        lane_id,
                        source_path: request.source_path.display().to_string(),
                        stage: "failed".to_string(),
                        progress: 0.0,
                        detail: "任务失败。".to_string(),
                        processed_ms: 0,
                        total_ms: 0,
                        payload: json!({}),
                        result: None,
                        error: Some(error),
                    },
                );
            }
        }

        set_active_job(lane_id, None);
        pending.fetch_sub(1, Ordering::SeqCst);
    }

    runtime.shutdown();
}

fn active_jobs() -> &'static Mutex<Vec<Option<ActiveJobControl>>> {
    ACTIVE_JOBS.get_or_init(|| Mutex::new(vec![None; WORKER_LANE_COUNT + 1]))
}

fn set_active_job(lane_id: usize, job: Option<ActiveJobControl>) {
    let mut jobs = match active_jobs().lock() {
        Ok(value) => value,
        Err(_) => return,
    };
    if lane_id >= jobs.len() {
        return;
    }
    jobs[lane_id] = job;
}

fn active_job(lane_id: usize) -> Result<ActiveJobControl> {
    let jobs = active_jobs()
        .lock()
        .map_err(|_| anyhow!("无法读取当前 Worker 状态。"))?;
    if lane_id == 0 || lane_id >= jobs.len() {
        return Err(anyhow!("无效的 Worker 编号：{lane_id}。"));
    }
    jobs[lane_id]
        .clone()
        .ok_or_else(|| anyhow!("Worker {lane_id} 当前没有正在转写的任务。"))
}

fn write_control_flag(active: &ActiveJobControl, file_name: &str) -> Result<()> {
    let path = control_flag_path(active, file_name);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create control directory {}", parent.display()))?;
    }
    fs::write(&path, b"1")
        .with_context(|| format!("failed to write control flag {}", path.display()))?;
    session_log::info(&format!(
        "worker control flag written | job_id={} | path={}",
        active.job_id,
        path.display()
    ));
    Ok(())
}

fn remove_control_flag(active: &ActiveJobControl, file_name: &str) -> Result<()> {
    let path = control_flag_path(active, file_name);
    if path.exists() {
        fs::remove_file(&path)
            .with_context(|| format!("failed to remove control flag {}", path.display()))?;
    }
    session_log::info(&format!(
        "worker control flag removed | job_id={} | path={}",
        active.job_id,
        path.display()
    ));
    Ok(())
}

fn control_flag_path(active: &ActiveJobControl, file_name: &str) -> PathBuf {
    active
        .project_root
        .join("outputs")
        .join(".jobs")
        .join(&active.job_id)
        .join(file_name)
}

impl WorkerRuntime {
    fn run_job<F>(
        &mut self,
        project_root: &Path,
        request: &RunJobRequest,
        lane_id: usize,
        on_progress: F,
    ) -> Result<JobResult>
    where
        F: FnMut(JobProgress),
    {
        if self.project_root.as_deref() != Some(project_root) {
            self.shutdown();
            self.project_root = Some(project_root.to_path_buf());
        }

        if self.worker.is_none() {
            self.worker = Some(start_persistent_worker(project_root, lane_id)?);
        }

        let worker = self
            .worker
            .as_mut()
            .context("persistent worker should be initialized")?;

        let result = worker.execute_job(request, on_progress);
        if result.is_err() && !worker.is_alive() {
            session_log::warn("persistent Python worker exited; it will be restarted for the next queued job");
            self.worker = None;
        }
        result
    }

    fn shutdown(&mut self) {
        if let Some(mut worker) = self.worker.take() {
            let _ = writeln!(worker.stdin, "{}", json!({"type": "shutdown", "payload": {}}));
            let _ = worker.stdin.flush();
            let _ = worker.child.kill();
            let _ = worker.child.wait();
        }
    }
}

impl PersistentWorker {
    fn execute_job<F>(&mut self, request: &RunJobRequest, mut on_progress: F) -> Result<JobResult>
    where
        F: FnMut(JobProgress),
    {
        self.stderr_buffer.clear();
        let request_line = serde_json::to_string(&json!({
            "type": "run_job",
            "payload": request,
        }))
        .context("failed to serialize run_job payload")?;

        writeln!(self.stdin, "{request_line}").context("failed to send run_job message")?;
        self.stdin.flush().context("failed to flush worker stdin")?;
        session_log::info(&format!(
            "persistent worker request sent | candidate={} | job_id={} | asr_backend={} | asr_profile={} | context_chars={} | terms={}",
            self.candidate_label,
            request.job_id,
            request.asr_backend,
            request
                .cloud_asr_profile
                .as_ref()
                .map(|profile| profile.name.as_str())
                .unwrap_or(""),
            request.context_text.chars().count(),
            request.terms.len()
        ));

        let mut last_logged_progress_percent: i32 = -1;
        loop {
            self.drain_stderr_to_log();

            let mut line = String::new();
            let bytes_read = self
                .stdout
                .read_line(&mut line)
                .context("failed to read worker output")?;
            if bytes_read == 0 {
                bail!(format_worker_error(
                    "persistent Python worker stdout closed before job completion",
                    &self.drain_stderr_to_string(),
                ));
            }

            let line = line.trim();
            if line.is_empty() {
                continue;
            }

            let envelope: WorkerEnvelope =
                serde_json::from_str(line).context("failed to decode worker message")?;

            match envelope.kind.as_str() {
                "job_event" => {
                    let mut progress = parse_progress(&envelope.payload);
                    progress.worker_lane = self.lane_id;
                    let progress_percent = (progress.progress * 100.0).floor() as i32;
                    let should_log_progress = progress.stage != "transcribing"
                        || progress_percent >= last_logged_progress_percent + 10
                        || progress_percent >= 99;
                    if should_log_progress {
                        session_log::info(&format!(
                            "worker event received | lane={} | candidate={} | stage={} | progress={:.3} | detail={}",
                            self.lane_id,
                            self.candidate_label,
                            progress.stage,
                            progress.progress,
                            progress.detail
                        ));
                        last_logged_progress_percent =
                            last_logged_progress_percent.max(progress_percent);
                    }
                    on_progress(progress);
                }
                "job_completed" => {
                    session_log::info(&format!(
                        "worker completed message received | lane={} | candidate={} | job_id={}",
                        self.lane_id, self.candidate_label, request.job_id
                    ));
                    let mut result: JobResult = serde_json::from_value(envelope.payload)
                        .context("failed to parse completion payload")?;
                    result.worker_lane = self.lane_id;
                    return Ok(result);
                }
                "job_failed" => {
                    let reason = envelope
                        .payload
                        .get("reason")
                        .or_else(|| envelope.payload.get("user_message"))
                        .and_then(Value::as_str)
                        .unwrap_or("worker reported a failure");
                    bail!(format_worker_error(reason, &self.drain_stderr_to_string()));
                }
                "error" => {
                    let reason = envelope
                        .payload
                        .get("reason")
                        .and_then(Value::as_str)
                        .unwrap_or("worker reported an unexpected error");
                    bail!(format_worker_error(reason, &self.drain_stderr_to_string()));
                }
                _ => {}
            }
        }
    }

    fn is_alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    fn drain_stderr_to_log(&mut self) {
        while let Ok(line) = self.stderr_rx.try_recv() {
            if !line.trim().is_empty() {
                session_log::warn(&format!(
                    "worker stderr | candidate={} | {}",
                    self.candidate_label, line
                ));
                self.stderr_buffer.push_str(&line);
                self.stderr_buffer.push('\n');
            }
        }
    }

    fn drain_stderr_to_string(&mut self) -> String {
        self.drain_stderr_to_log();
        self.stderr_buffer.trim().to_string()
    }
}

fn start_persistent_worker(project_root: &Path, lane_id: usize) -> Result<PersistentWorker> {
    let mut last_error: Option<anyhow::Error> = None;
    let candidates = python_candidates(project_root);
    session_log::info(&format!(
        "starting persistent worker | lane={} | project_root={} | candidates={}",
        lane_id,
        project_root.display(),
        candidates
            .iter()
            .map(|candidate| candidate.label.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    ));

    for candidate in candidates {
        match start_worker_with_candidate(project_root, &candidate, lane_id) {
            Ok(worker) => return Ok(worker),
            Err(error) => {
                session_log::warn(&format!(
                    "persistent worker candidate failed | lane={} | candidate={} | error={error:#}",
                    lane_id, candidate.label
                ));
                last_error = Some(error);
            }
        }
    }

    Err(last_error.unwrap_or_else(|| anyhow!("无法启动 Python worker。")))
}

fn start_worker_with_candidate(
    project_root: &Path,
    candidate: &PythonCandidate,
    lane_id: usize,
) -> Result<PersistentWorker> {
    let worker_dir = project_root.join("apps").join("worker-python");
    let mut command = Command::new(&candidate.program);
    command
        .args(&candidate.prefix_args)
        .arg("-X")
        .arg("utf8")
        .arg("-m")
        .arg("app.main")
        .current_dir(&worker_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if let Some(worker_log_path) = session_log::worker_log_path() {
        command.env("ASR_LOCAL_WORKER_LOG", &worker_log_path);
    }
    command.env("PYTHONUTF8", "1");
    command.env("PYTHONIOENCODING", "utf-8");

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let mut child = command
        .spawn()
        .with_context(|| format!("failed to start worker with {}", candidate.label))?;
    let stdin = child.stdin.take().context("worker stdin is unavailable")?;
    let stdout = child.stdout.take().context("worker stdout is unavailable")?;
    let stderr = child.stderr.take().context("worker stderr is unavailable")?;
    let stderr_rx = spawn_stderr_collector(stderr);

    session_log::info(&format!(
        "persistent worker process started | lane={} | candidate={} | worker_dir={} | pid={}",
        lane_id,
        candidate.label,
        worker_dir.display(),
        child.id()
    ));

    Ok(PersistentWorker {
        child,
        stdin,
        stdout: BufReader::new(stdout),
        stderr_rx,
        stderr_buffer: String::new(),
        candidate_label: candidate.label.clone(),
        lane_id,
    })
}

fn spawn_stderr_collector<R>(reader: R) -> mpsc::Receiver<String>
where
    R: Read + Send + 'static,
{
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(reader).lines() {
            match line {
                Ok(value) => {
                    let _ = tx.send(value);
                }
                Err(error) => {
                    let _ = tx.send(format!("failed to read stderr: {error}"));
                    break;
                }
            }
        }
    });
    rx
}

fn python_candidates(project_root: &Path) -> Vec<PythonCandidate> {
    let mut local_candidates = Vec::new();
    let worker_venv = project_root
        .join("apps")
        .join("worker-python")
        .join(".venv")
        .join("Scripts")
        .join("python.exe");
    if worker_venv.exists() {
        local_candidates.push(PythonCandidate {
            label: worker_venv.display().to_string(),
            program: worker_venv.display().to_string(),
            prefix_args: Vec::new(),
        });
    }

    let workspace_venv = project_root
        .join(".venv-qwen-smoke")
        .join("Scripts")
        .join("python.exe");
    if workspace_venv.exists() {
        local_candidates.push(PythonCandidate {
            label: workspace_venv.display().to_string(),
            program: workspace_venv.display().to_string(),
            prefix_args: Vec::new(),
        });
    }

    if !local_candidates.is_empty() {
        return local_candidates;
    }

    vec![
        PythonCandidate {
            label: "python".to_string(),
            program: "python".to_string(),
            prefix_args: Vec::new(),
        },
        PythonCandidate {
            label: "py -3".to_string(),
            program: "py".to_string(),
            prefix_args: vec!["-3".to_string()],
        },
    ]
}

fn parse_progress(payload: &Value) -> JobProgress {
    let stage = payload
        .get("stage")
        .and_then(Value::as_str)
        .unwrap_or("working")
        .to_string();
    let progress = payload
        .get("progress")
        .and_then(Value::as_f64)
        .unwrap_or(0.0)
        .clamp(0.0, 1.0) as f32;
    let processed_ms = payload
        .get("processed_ms")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    let total_ms = payload
        .get("total_ms")
        .and_then(Value::as_u64)
        .unwrap_or_default();

    let mut details = vec![match stage.as_str() {
        "paused" => "任务已暂停，点击继续可恢复。",
        "resumed" => "任务已继续。",
        "terminating" => "正在终止当前任务。",
        _ => stage_label(&stage),
    }
    .to_string()];
    if let (Some(current), Some(total)) = (
        payload.get("current_segment_index").and_then(Value::as_u64),
        payload.get("segment_count").and_then(Value::as_u64),
    ) {
        details.push(format!("segment {current}/{total}"));
    }
    if total_ms > 0 {
        details.push(format!(
            "{} / {}",
            format_duration(processed_ms),
            format_duration(total_ms)
        ));
    }
    if let Some(speaker_count) = payload.get("speaker_count").and_then(Value::as_u64) {
        details.push(format!("{speaker_count} speaker(s)"));
    }

    JobProgress {
        worker_lane: 0,
        stage,
        progress,
        detail: details.join(" | "),
        processed_ms,
        total_ms,
        payload: payload.clone(),
    }
}

fn emit_progress(app: &AppHandle, request: &RunJobRequest, progress: JobProgress) {
    emit_worker_event(
        app,
        WorkerUiEvent {
            event: "progress".to_string(),
            job_id: request.job_id.clone(),
            lane_id: progress.worker_lane,
            source_path: request.source_path.display().to_string(),
            stage: progress.stage,
            progress: progress.progress,
            detail: progress.detail,
            processed_ms: progress.processed_ms,
            total_ms: progress.total_ms,
            payload: progress.payload,
            result: None,
            error: None,
        },
    );
}

fn emit_worker_event(app: &AppHandle, event: WorkerUiEvent) {
    if let Err(error) = app.emit(WORKER_EVENT_NAME, event) {
        session_log::warn(&format!("failed to emit worker event: {error}"));
    }
}

fn stage_label(stage: &str) -> &str {
    match stage {
        "preparing" => "准备运行环境",
        "decoding" => "解析音频",
        "diarizing" => "执行说话人分离",
        "segmenting" => "整理说话片段",
        "transcribing" => "执行 ASR 转写",
        "merging" => "合并转写结果",
        "normalizing" => "应用后处理",
        "exporting" => "写出结果文件",
        "failed" => "任务失败",
        _ => "执行中",
    }
}

fn format_duration(value_ms: u64) -> String {
    let total_seconds = value_ms / 1000;
    let hours = total_seconds / 3600;
    let minutes = (total_seconds % 3600) / 60;
    let seconds = total_seconds % 60;
    format!("{hours:02}:{minutes:02}:{seconds:02}")
}

fn format_worker_error(reason: &str, stderr_output: &str) -> String {
    let trimmed_stderr = stderr_output.trim();
    if trimmed_stderr.is_empty() {
        reason.to_string()
    } else {
        format!("{reason}\n\nWorker stderr:\n{trimmed_stderr}")
    }
}
