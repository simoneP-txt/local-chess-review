//! The engine as two calls: one command line in, one answer line out.

/// A UCI engine. `read_line` blocks until a line arrives and gives an Err once the engine is gone.
pub trait EngineIo: Send {
    fn send(&mut self, line: &str) -> Result<(), String>;
    fn read_line(&mut self) -> Result<String, String>;
}

#[cfg(any(target_os = "android", target_os = "ios"))]
pub use linked::LinkedEngine;

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
