use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, State, WindowEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

struct ApiState {
    port: u16,
    token: String,
    data_dir: PathBuf,
    child: Mutex<Option<CommandChild>>,
}

#[derive(Serialize)]
struct ApiConfig {
    port: u16,
    token: String,
}

fn terminate_child_tree(child: CommandChild) {
    let pid = child.pid().to_string();
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        let _ = Command::new("taskkill.exe")
            .args(["/PID", pid.as_str(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    let _ = child.kill();
}

#[tauri::command]
fn get_api_config(state: State<'_, ApiState>) -> ApiConfig {
    ApiConfig {
        port: state.port,
        token: state.token.clone(),
    }
}

#[tauri::command]
fn register_codex_mcp(state: State<'_, ApiState>) -> Result<String, String> {
    let executable = std::env::current_exe()
        .map_err(|error| error.to_string())?
        .parent()
        .ok_or_else(|| "Application directory is unavailable".to_string())?
        .join("researchbrain-sidecar.exe");
    if !executable.is_file() {
        return Err(format!(
            "ResearchBrain sidecar not found: {}",
            executable.display()
        ));
    }

    let _ = Command::new("cmd.exe")
        .args(["/d", "/c", "codex.cmd", "mcp", "remove", "researchbrain"])
        .output();
    let data_argument = format!("RESEARCHBRAIN_DATA_DIR={}", state.data_dir.display());
    let output = Command::new("cmd.exe")
        .args([
            "/d",
            "/c",
            "codex.cmd",
            "mcp",
            "add",
            "researchbrain",
            "--env",
        ])
        .arg(data_argument)
        .arg("--")
        .arg(executable)
        .arg("mcp")
        .output()
        .map_err(|error| format!("Unable to start codex.cmd: {error}"))?;
    if !output.status.success() {
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if message.is_empty() {
            String::from_utf8_lossy(&output.stdout).trim().to_string()
        } else {
            message
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![get_api_config, register_codex_mcp])
        .setup(|app| {
            let listener = TcpListener::bind("127.0.0.1:0")?;
            let port = listener.local_addr()?.port();
            drop(listener);

            let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
            let port_string = port.to_string();
            let data_dir = std::env::var_os("LOCALAPPDATA")
                .map(std::path::PathBuf::from)
                .map(|path| path.join("ResearchBrain"))
                .unwrap_or(app.path().app_local_data_dir()?);
            std::fs::create_dir_all(&data_dir)?;
            let sidecar = app
                .shell()
                .sidecar("researchbrain-sidecar")?
                .args([
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    port_string.as_str(),
                ])
                .env("RESEARCHBRAIN_SESSION_TOKEN", &token)
                .env("RESEARCHBRAIN_PARENT_PID", std::process::id().to_string())
                .env("RESEARCHBRAIN_DATA_DIR", &data_dir);
            let (mut events, child) = sidecar.spawn()?;
            tauri::async_runtime::spawn(async move { while events.recv().await.is_some() {} });
            app.manage(ApiState {
                port,
                token,
                data_dir,
                child: Mutex::new(Some(child)),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Destroyed) {
                let state = window.state::<ApiState>();
                let child = {
                    let mut guard = state.child.lock().expect("sidecar state poisoned");
                    guard.take()
                };
                if let Some(child) = child {
                    terminate_child_tree(child);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ResearchBrain");
}
