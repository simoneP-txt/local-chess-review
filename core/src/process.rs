//! An engine executable run as a child process, on the desktop.
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdout, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use crate::uci::EngineIo;

const CLOSED: &str = "the engine is closed";

/// The engine's stdin (in `child`) and stdout; close drops both.
pub struct ProcessIo {
    child: Child,
    stdout: Option<BufReader<ChildStdout>>,
}

impl ProcessIo {
    pub fn spawn(exe: &Path) -> Result<ProcessIo, String> {
        let mut child = Command::new(exe)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("cannot start {}: {e}", exe.display()))?;
        let stdout = child.stdout.take().map(BufReader::new);
        Ok(ProcessIo { child, stdout })
    }

    /// quit, wait up to 2 s, then kill and wait; afterwards read_line gives Err.
    pub fn close(&mut self) -> Result<(), String> {
        if let Some(mut stdin) = self.child.stdin.take() {
            // the engine may be gone already
            let _ = stdin.write_all(b"quit\n");
        }
        self.stdout = None;
        let deadline = Instant::now() + Duration::from_secs(2);
        while self.child.try_wait().map_err(|e| e.to_string())?.is_none() {
            if Instant::now() >= deadline {
                self.child.kill().map_err(|e| e.to_string())?;
                self.child.wait().map_err(|e| e.to_string())?;
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        Ok(())
    }
}

impl EngineIo for ProcessIo {
    fn send(&mut self, line: &str) -> Result<(), String> {
        let stdin = self.child.stdin.as_mut().ok_or(CLOSED)?;
        stdin
            .write_all(format!("{line}\n").as_bytes())
            .map_err(|e| e.to_string())
    }

    fn read_line(&mut self) -> Result<String, String> {
        let stdout = self.stdout.as_mut().ok_or(CLOSED)?;
        let mut line = String::new();
        match stdout.read_line(&mut line) {
            Ok(0) => Err("the engine exited".to_string()),
            Ok(_) => Ok(line.trim_end_matches(['\r', '\n']).to_string()),
            Err(e) => Err(e.to_string()),
        }
    }
}

impl Drop for ProcessIo {
    fn drop(&mut self) {
        let _ = self.close();
    }
}
