// No console window next to the desktop app in a release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    spike_lib::run()
}
