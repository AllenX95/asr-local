use anyhow::{anyhow, bail, Context, Result};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{mpsc, Mutex, OnceLock};
use std::thread;
use tauri::{AppHandle, Emitter};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct V2Process {
    child: Child,
    stdin: ChildStdin,
    lines: mpsc::Receiver<String>,
    next_request_id: u64,
    app: AppHandle,
}

static PROCESS: OnceLock<Mutex<Option<V2Process>>> = OnceLock::new();

pub fn request(
    app: AppHandle,
    project_root: &Path,
    method: &str,
    operation_id: Option<&str>,
    params: Value,
) -> Result<Value> {
    let process_mutex = PROCESS.get_or_init(|| Mutex::new(None));
    let mut guard = process_mutex.lock().map_err(|_| anyhow!("v2 worker mutex poisoned"))?;
    if guard.is_none() {
        let mut process = spawn(project_root, app.clone())?;
        let hello = send_and_wait(&mut process, "runtime.hello", None, json!({"supported_versions": [2]}))?;
        if hello.get("selected_version") != Some(&json!(2)) {
            let _ = process.child.kill();
            bail!("v2 worker did not negotiate protocol version 2");
        }
        *guard = Some(process);
    }
    let process = guard.as_mut().expect("process initialized");
    let result = send_and_wait(process, method, operation_id, params)?;
    if method == "runtime.shutdown" {
        let _ = process.child.kill();
        *guard = None;
    }
    Ok(result)
}

pub fn shutdown(app: AppHandle, project_root: &Path) -> Result<Value> {
    let Some(process_mutex) = PROCESS.get() else {
        return Ok(json!({"state": "stopped", "active_workflow_ids": []}));
    };
    if process_mutex.lock().map_err(|_| anyhow!("v2 worker mutex poisoned"))?.is_none() {
        return Ok(json!({"state": "stopped", "active_workflow_ids": []}));
    }
    request(app, project_root, "runtime.shutdown", None, json!({"mode": "interrupt", "grace_ms": 10000}))
}

fn spawn(project_root: &Path, app: AppHandle) -> Result<V2Process> {
    let worker_dir = project_root.join("apps").join("worker-python");
    let (program, prefix_args) = python_candidate(project_root)?;
    let mut command = Command::new(&program);
    command
        .args(prefix_args)
        .arg("-X")
        .arg("utf8")
        .arg("-m")
        .arg("app.main")
        .arg("--contract")
        .arg("v2")
        .arg("--pipeline-mode")
        .arg(std::env::var("ASR_LOCAL_V2_PIPELINE_MODE").unwrap_or_else(|_| "auto".to_string()))
        .current_dir(&worker_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command.spawn().with_context(|| format!("failed to start v2 worker with {}", program.display()))?;
    let stdin = child.stdin.take().ok_or_else(|| anyhow!("v2 worker stdin unavailable"))?;
    let stdout = child.stdout.take().ok_or_else(|| anyhow!("v2 worker stdout unavailable"))?;
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().flatten() {
                eprintln!("[workflow-v2] {line}");
            }
        });
    }
    let (tx, rx) = mpsc::channel();
    let event_app = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            match serde_json::from_str::<Value>(&line) {
                Ok(message) if message.get("kind").and_then(Value::as_str) == Some("event") => {
                    if let Some(payload) = message.get("payload") {
                        let _ = event_app.emit("workflow-event-v2", payload.clone());
                    }
                }
                _ => {
                    if tx.send(line).is_err() {
                        break;
                    }
                }
            }
        }
    });
    Ok(V2Process { child, stdin, lines: rx, next_request_id: 0, app })
}

fn send_and_wait(process: &mut V2Process, method: &str, operation_id: Option<&str>, params: Value) -> Result<Value> {
    process.next_request_id += 1;
    let request_id = format!("req_v2_{}", process.next_request_id);
    let mut request = json!({
        "protocol": "asr-local-workflow",
        "protocol_version": 2,
        "kind": "request",
        "request_id": request_id,
        "method": method,
        "params": params,
    });
    if let Some(operation_id) = operation_id {
        request["operation_id"] = json!(operation_id);
    }
    let encoded = serde_json::to_string(&request)?;
    process.stdin.write_all(encoded.as_bytes())?;
    process.stdin.write_all(b"\n")?;
    process.stdin.flush()?;

    loop {
        let line = process.lines.recv().map_err(|_| anyhow!("v2 worker stdout closed"))?;
        let message: Value = serde_json::from_str(&line).context("failed to decode v2 worker message")?;
        if message.get("kind").and_then(Value::as_str) == Some("event") {
            if let Some(payload) = message.get("payload") {
                let _ = process.app.emit("workflow-event-v2", payload.clone());
            }
            continue;
        }
        if message.get("request_id").and_then(Value::as_str) != Some(&request_id) {
            bail!("v2 worker response request_id mismatch");
        }
        if message.get("ok").and_then(Value::as_bool) != Some(true) {
            let error = message.get("error").cloned().unwrap_or_else(|| json!({}));
            bail!("v2 worker request failed: {}", error);
        }
        return Ok(message.get("result").cloned().unwrap_or_else(|| json!({})));
    }
}

fn python_candidate(project_root: &Path) -> Result<(PathBuf, Vec<String>)> {
    if let Ok(configured) = std::env::var("ASR_LOCAL_PYTHON") {
        let configured = PathBuf::from(configured.trim());
        if configured.exists() {
            return Ok((configured, Vec::new()));
        }
        bail!("ASR_LOCAL_PYTHON does not exist: {}", configured.display());
    }
    let candidates = [
        (project_root.join("apps/worker-python/.venv/Scripts/python.exe"), Vec::new()),
        (PathBuf::from("python"), Vec::new()),
        (PathBuf::from("py"), vec!["-3".to_string()]),
    ];
    for (program, args) in candidates {
        if program.is_absolute() && !program.exists() {
            continue;
        }
        return Ok((program, args));
    }
    bail!("no Python runtime candidate found")
}
