#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod asr_profiles;
mod commands;
mod config;
mod history;
mod session_log;
mod summary_api;
mod summary_profiles;
mod summary_templates;
mod worker_client;
mod worker_contract;
mod workflow_contract_v2;
mod workflow_v2_client;
mod workflow_v2_commands;

use tauri::Manager;

fn main() {
    let project_root = config::project_root();
    if let Err(error) = session_log::init(&project_root) {
        eprintln!("failed to initialize session log: {error}");
    }

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            commands::get_app_info,
            commands::select_audio_file,
            commands::select_markdown_file,
            commands::select_output_dir,
            commands::read_text_file,
            commands::save_text_file,
            commands::open_path,
            commands::load_models_config,
            commands::save_model_paths,
            commands::load_asr_profiles,
            commands::save_asr_profile,
            commands::delete_asr_profile,
            commands::worker_health_check,
            commands::submit_job,
            commands::pause_lane,
            commands::resume_lane,
            commands::terminate_lane,
            commands::load_summary_profiles,
            commands::save_summary_profile,
            commands::delete_summary_profile,
            commands::load_summary_templates,
            commands::save_summary_template,
            commands::delete_summary_template,
            commands::generate_summary,
            commands::list_history_items,
            workflow_v2_commands::workflow_v2_capabilities,
            workflow_v2_commands::workflow_v2_catalogs,
            workflow_v2_commands::workflow_v2_prompt_preview,
            workflow_v2_commands::workflow_v2_submit,
            workflow_v2_commands::workflow_v2_list,
            workflow_v2_commands::workflow_v2_get,
            workflow_v2_commands::workflow_v2_control,
            workflow_v2_commands::workflow_v2_retry,
            workflow_v2_commands::workflow_v2_register_revision,
            workflow_v2_commands::workflow_v2_provide_secret,
            workflow_v2_commands::workflow_v2_shutdown,
        ])
        .setup(|_app| {
            session_log::info("tauri desktop app startup");
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let _ = workflow_v2_client::shutdown(window.app_handle().clone(), &config::project_root());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
