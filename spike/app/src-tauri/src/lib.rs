pub mod engine;
pub mod protocol;

use serde_json::Value;

/// The cores the spike counts: /sys/devices/system/cpu/possible on Android, else available_parallelism.
#[tauri::command]
fn cores() -> u32 {
    #[cfg(target_os = "android")]
    if let Some(n) = std::fs::read_to_string("/sys/devices/system/cpu/possible")
        .ok()
        .and_then(|s| count_cpus(&s))
    {
        return n;
    }
    std::thread::available_parallelism().map_or(1, |n| n.get() as u32)
}

/// Counts a kernel CPU list such as "0-7" or "0-3,6".
#[cfg(any(target_os = "android", test))]
fn count_cpus(list: &str) -> Option<u32> {
    list.trim()
        .split(',')
        .map(|range| {
            let (first, last) = range.split_once('-').unwrap_or((range, range));
            last.parse::<u32>()
                .ok()?
                .checked_sub(first.parse().ok()?)
                .map(|n| n + 1)
        })
        .sum()
}

/// Analyses the Opera Game on `instances` engines and returns the RawRun; each engine line goes to `on_line`.
#[tauri::command]
async fn run_opera(
    threads: u32,
    instances: u32,
    label: String,
    on_line: tauri::ipc::Channel<String>,
) -> Result<Value, String> {
    // The analysis blocks for a minute: off the async runtime, so the page stays free.
    tauri::async_runtime::spawn_blocking(move || {
        opera(threads, instances, &label, &|line| {
            let _ = on_line.send(line.to_owned());
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(any(target_os = "android", target_os = "ios"))]
fn opera(
    threads: u32,
    instances: u32,
    label: &str,
    on_line: &(dyn Fn(&str) + Sync),
) -> Result<Value, String> {
    let engines = (0..instances)
        .map(|_| engine::LinkedEngine::new().map(|e| Box::new(e) as Box<dyn engine::EngineIo>))
        .collect::<Result<Vec<_>, _>>()?;
    protocol::run(engines, threads, label, on_line)
}

/// The running app, for the sidecar. A static rather than an argument of opera(): the iOS auto
/// mode calls opera() before any app exists.
#[cfg(not(any(target_os = "android", target_os = "ios")))]
static APP: std::sync::OnceLock<tauri::AppHandle> = std::sync::OnceLock::new();

#[cfg(not(any(target_os = "android", target_os = "ios")))]
fn opera(
    threads: u32,
    instances: u32,
    label: &str,
    on_line: &(dyn Fn(&str) + Sync),
) -> Result<Value, String> {
    let app = APP.get().ok_or("the sidecar needs the running app")?;
    let engines = (0..instances)
        .map(|_| engine::SidecarEngine::new(app).map(|e| Box::new(e) as Box<dyn engine::EngineIo>))
        .collect::<Result<Vec<_>, _>>()?;
    protocol::run(engines, threads, label, on_line)
}

#[cfg(all(feature = "frame", feature = "snap-layout"))]
compile_error!("frame and snap-layout are two builds of the title bar: enable one of them");

/// main.mm calls this on iOS. tauri::mobile_entry_point would call tauri::log_stdout() first,
/// which sends stdout to os_log, so the auto mode runs before it.
#[cfg(target_os = "ios")]
#[unsafe(no_mangle)]
pub extern "C" fn start_app() {
    if std::env::var("SPIKE_AUTO").is_ok_and(|v| v == "1") {
        run(); // the auto mode ends the process
    }
    tauri::log_stdout();
    run()
}

#[cfg_attr(target_os = "android", tauri::mobile_entry_point)]
pub fn run() {
    if std::env::var("SPIKE_AUTO").is_ok_and(|v| v == "1") {
        // S2 on the iOS simulator: no page, one thread, the RawRun on stdout.
        match opera(1, 1, "ios-sim-app-t1-1", &|_| {}) {
            Ok(raw) => {
                println!("SPIKE_RAW {raw}");
                std::process::exit(0);
            }
            Err(e) => {
                eprintln!("SPIKE_ERROR {e}");
                std::process::exit(1);
            }
        }
    }
    let builder = tauri::Builder::default().plugin(tauri_plugin_sql::Builder::default().build());
    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    let builder = builder.plugin(tauri_plugin_shell::init()).setup(|app| {
        let _ = APP.set(app.handle().clone());
        // capabilities/ cannot name the permission of a plugin that only one build has.
        #[cfg(all(windows, feature = "snap-layout"))]
        tauri::Manager::add_capability(
            app,
            tauri::ipc::CapabilityBuilder::new("snap-layout")
                .window("main")
                .permission("snap-layout:default"),
        )?;
        Ok(())
    });
    #[cfg(all(windows, feature = "frame"))]
    let builder = builder.plugin(
        tauri_plugin_frame::FramePluginBuilder::new()
            .auto_titlebar(true)
            .build(),
    );
    #[cfg(all(windows, feature = "snap-layout"))]
    let builder = builder.plugin(tauri_plugin_snap_layout::init().button_id("max").build());
    builder
        .invoke_handler(tauri::generate_handler![cores, run_opera])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    #[test]
    fn count_cpus_reads_kernel_lists() {
        assert_eq!(super::count_cpus("0-7\n"), Some(8));
        assert_eq!(super::count_cpus("0-3,6"), Some(5));
        assert_eq!(super::count_cpus("0"), Some(1));
        assert_eq!(super::count_cpus(""), None);
    }
}
