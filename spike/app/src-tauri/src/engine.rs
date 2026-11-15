//! The engine as two calls: one command line in, one answer line out.

/// A UCI engine. `read_line` blocks until a line arrives and gives an Err once the engine is gone.
pub trait EngineIo: Send {
    fn send(&mut self, line: &str) -> Result<(), String>;
    fn read_line(&mut self) -> Result<String, String>;
}

#[cfg(any(target_os = "android", target_os = "ios"))]
pub use linked::LinkedEngine;

#[cfg(not(any(target_os = "android", target_os = "ios")))]
pub use sidecar::SidecarEngine;

#[cfg(not(any(target_os = "android", target_os = "ios")))]
mod sidecar {
    use tauri::async_runtime::Receiver;
    use tauri_plugin_shell::process::{CommandChild, CommandEvent};
    use tauri_plugin_shell::ShellExt;

    use super::EngineIo;

    /// The official Stockfish next to the app (bundle > externalBin), run by the shell plugin.
    pub struct SidecarEngine {
        events: Receiver<CommandEvent>,
        child: CommandChild,
        pending: Vec<u8>,
    }

    impl SidecarEngine {
        pub fn new(app: &tauri::AppHandle) -> Result<Self, String> {
            let (events, child) = app
                .shell()
                .sidecar("stockfish")
                .map_err(|e| e.to_string())?
                // Raw chunks: the line mode also ends a line at a \r whose \n is still in the pipe.
                .set_raw_out(true)
                .kill_on_drop(true)
                .spawn()
                .map_err(|e| e.to_string())?;
            Ok(Self {
                events,
                child,
                pending: Vec::new(),
            })
        }
    }

    impl EngineIo for SidecarEngine {
        fn send(&mut self, line: &str) -> Result<(), String> {
            self.child
                .write(format!("{line}\n").as_bytes())
                .map_err(|e| e.to_string())
        }

        fn read_line(&mut self) -> Result<String, String> {
            loop {
                if let Some(line) = take_line(&mut self.pending) {
                    return Ok(line);
                }
                // Called from spawn_blocking or a plain thread, never on the async runtime.
                match self.events.blocking_recv() {
                    Some(CommandEvent::Stdout(bytes)) => self.pending.extend(bytes),
                    Some(CommandEvent::Stderr(_)) => {}
                    Some(CommandEvent::Error(e)) => return Err(e),
                    _ => return Err("the engine exited".into()), // Terminated, or the channel closed
                }
            }
        }
    }

    /// Takes the first whole line out of `pending`, without its \r\n.
    fn take_line(pending: &mut Vec<u8>) -> Option<String> {
        let end = pending.iter().position(|&b| b == b'\n')?;
        let line: Vec<u8> = pending.drain(..=end).collect();
        Some(
            String::from_utf8_lossy(&line)
                .trim_end_matches(['\r', '\n'])
                .to_owned(),
        )
    }

    #[cfg(test)]
    mod tests {
        #[test]
        fn take_line_waits_for_the_newline() {
            let mut pending = b"info depth 1\r".to_vec();
            assert_eq!(super::take_line(&mut pending), None);
            pending.extend(b"\nbestmove e2e4\r\n\r\nid");
            assert_eq!(super::take_line(&mut pending).unwrap(), "info depth 1");
            assert_eq!(super::take_line(&mut pending).unwrap(), "bestmove e2e4");
            assert_eq!(super::take_line(&mut pending).unwrap(), "");
            assert_eq!(super::take_line(&mut pending), None);
            assert_eq!(pending, b"id");
        }
    }
}

#[cfg(any(target_os = "android", target_os = "ios"))]
mod linked {
    use std::ffi::{c_char, c_int, c_void, CString};

    use super::EngineIo;

    // cpp/sfshim.cpp, built with Stockfish by build.rs.
    extern "C" {
        fn sf_new() -> *mut c_void;
        fn sf_send(h: *mut c_void, line: *const c_char);
        fn sf_read_line(h: *mut c_void, buf: *mut c_char, cap: c_int) -> c_int;
        fn sf_free(h: *mut c_void);
    }

    /// Stockfish 19 compiled into the app.
    pub struct LinkedEngine {
        handle: *mut c_void,
        buf: Vec<u8>,
    }

    // The shim locks its own queue, so the handle may move to another thread.
    unsafe impl Send for LinkedEngine {}

    impl LinkedEngine {
        pub fn new() -> Result<Self, String> {
            let handle = unsafe { sf_new() };
            if handle.is_null() {
                return Err("the linked engine did not start".into());
            }
            Ok(Self {
                handle,
                buf: vec![0; 64 * 1024],
            })
        }
    }

    impl EngineIo for LinkedEngine {
        fn send(&mut self, line: &str) -> Result<(), String> {
            let line = CString::new(line).map_err(|e| e.to_string())?;
            unsafe { sf_send(self.handle, line.as_ptr()) };
            Ok(())
        }

        fn read_line(&mut self) -> Result<String, String> {
            let n = unsafe {
                sf_read_line(
                    self.handle,
                    self.buf.as_mut_ptr().cast(),
                    self.buf.len() as c_int,
                )
            };
            match usize::try_from(n) {
                Err(_) => Err("the engine exited".into()),
                Ok(n) if n > self.buf.len() => Err(format!(
                    "an engine line of {n} bytes did not fit the buffer"
                )),
                Ok(n) => Ok(String::from_utf8_lossy(&self.buf[..n]).into_owned()),
            }
        }
    }

    impl Drop for LinkedEngine {
        fn drop(&mut self) {
            unsafe { sf_free(self.handle) }
        }
    }
}
