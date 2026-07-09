use std::fs::{self, File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(target_os = "windows")]
use std::os::windows::io::AsRawHandle;
#[cfg(target_os = "windows")]
use windows_sys::Win32::Foundation::HANDLE;
#[cfg(target_os = "windows")]
use windows_sys::Win32::System::Console::{SetStdHandle, STD_ERROR_HANDLE, STD_OUTPUT_HANDLE};

#[derive(Debug, Clone, serde::Serialize)]
pub struct SessionLogPaths {
    pub directory: PathBuf,
    pub desktop_log_path: PathBuf,
    pub worker_log_path: PathBuf,
    pub stdio_log_path: PathBuf,
}

static SESSION_LOGS: OnceLock<SessionLogPaths> = OnceLock::new();
static DESKTOP_LOG_FILE: OnceLock<Mutex<BufWriter<File>>> = OnceLock::new();
#[cfg(target_os = "windows")]
static STDIO_REDIRECT_FILES: OnceLock<(File, File)> = OnceLock::new();

pub fn init(project_root: &Path) -> std::io::Result<SessionLogPaths> {
    if let Some(existing) = SESSION_LOGS.get() {
        return Ok(existing.clone());
    }

    let directory = project_root.join("outputs").join("logs");
    fs::create_dir_all(&directory)?;

    let desktop_log_path = directory.join("tauri-desktop-session.log");
    let worker_log_path = directory.join("tauri-worker-session.log");
    let stdio_log_path = directory.join("tauri-runtime-stdio.log");

    let desktop_file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&desktop_log_path)?;
    let mut desktop_file = BufWriter::new(desktop_file);
    desktop_file.write_all(b"\xEF\xBB\xBF")?;
    desktop_file.flush()?;
    fs::write(&worker_log_path, b"\xEF\xBB\xBF")?;
    fs::write(&stdio_log_path, &[] as &[u8])?;

    let paths = SessionLogPaths {
        directory,
        desktop_log_path,
        worker_log_path,
        stdio_log_path,
    };

    let _ = DESKTOP_LOG_FILE.set(Mutex::new(desktop_file));
    #[cfg(target_os = "windows")]
    {
        if let Err(error) = redirect_process_stdio(&paths.stdio_log_path) {
            let _ = writeln!(
                std::io::stderr(),
                "failed to redirect runtime stdio to {}: {error}",
                paths.stdio_log_path.display()
            );
        }
    }
    let _ = SESSION_LOGS.set(paths.clone());

    info(&format!(
        "session log initialized | desktop={} | worker={} | stdio={}",
        paths.desktop_log_path.display(),
        paths.worker_log_path.display(),
        paths.stdio_log_path.display()
    ));

    Ok(paths)
}

pub fn paths() -> Option<&'static SessionLogPaths> {
    SESSION_LOGS.get()
}

pub fn worker_log_path() -> Option<PathBuf> {
    paths().map(|value| value.worker_log_path.clone())
}

pub fn info(message: &str) {
    write_line("INFO", message);
}

pub fn warn(message: &str) {
    write_line("WARN", message);
}

pub fn error(message: &str) {
    write_line("ERROR", message);
}

fn write_line(level: &str, message: &str) {
    let Some(file) = DESKTOP_LOG_FILE.get() else {
        return;
    };

    let mut guard = match file.lock() {
        Ok(value) => value,
        Err(_) => return,
    };

    let timestamp = unix_timestamp_ms();
    for line in message.lines() {
        let _ = writeln!(guard, "[{timestamp}] [{level}] {line}");
    }
    let _ = guard.flush();
}

fn unix_timestamp_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_millis())
        .unwrap_or_default()
}

#[cfg(target_os = "windows")]
fn redirect_process_stdio(stdio_log_path: &Path) -> std::io::Result<()> {
    let stdout_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(stdio_log_path)?;
    let stderr_file = stdout_file.try_clone()?;

    redirect_std_handle(STD_OUTPUT_HANDLE, stdout_file.as_raw_handle() as HANDLE)?;
    redirect_std_handle(STD_ERROR_HANDLE, stderr_file.as_raw_handle() as HANDLE)?;

    let _ = STDIO_REDIRECT_FILES.set((stdout_file, stderr_file));
    Ok(())
}

#[cfg(target_os = "windows")]
fn redirect_std_handle(kind: u32, handle: HANDLE) -> std::io::Result<()> {
    let result = unsafe { SetStdHandle(kind, handle) };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}
