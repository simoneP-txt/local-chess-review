use std::env;
use std::path::PathBuf;

// SRCS of the sf_19 Makefile without main.cpp: cpp/sfshim.cpp drives the Engine class instead.
const SOURCES: &[&str] = &[
    "attacks.cpp",
    "benchmark.cpp",
    "bitboard.cpp",
    "evaluate.cpp",
    "misc.cpp",
    "movegen.cpp",
    "movepick.cpp",
    "position.cpp",
    "search.cpp",
    "thread.cpp",
    "timeman.cpp",
    "tt.cpp",
    "uci.cpp",
    "ucioption.cpp",
    "tune.cpp",
    "syzygy/tbprobe.cpp",
    "nnue/nnue_accumulator.cpp",
    "nnue/nnue_misc.cpp",
    "nnue/network.cpp",
    "nnue/features/half_ka_v2_hm.cpp",
    "nnue/features/full_threats.cpp",
    "nnue/features/pp_3wide.cpp",
    "engine.cpp",
    "score.cpp",
    "memory.cpp",
];

// CXXFLAGS of `make build ARCH=armv8-dotprod COMP=ndk` in sf_19, the target the official binary
// picked on the phone in S0, minus -fPIE (cc builds -fPIC for a library) and -flto=full (not
// tried: rustc runs the final link).
const FLAGS: &[&str] = &[
    "-std=c++17",
    "-Wall",
    "-Wcast-qual",
    "-fno-exceptions",
    "-stdlib=libc++",
    "-DNDEBUG",
    "-O3",
    "-funroll-loops",
    "-DIS_64BIT",
    "-DUSE_POPCNT",
    "-DUSE_NEON=8",
    "-march=armv8.2-a+dotprod",
    "-DUSE_NEON_DOTPROD",
    "-DARCH=armv8-dotprod",
    "-Xclang",
    "-mllvm",
    "-Xclang",
    "-inline-threshold=500",
];

fn main() {
    let os = env::var("CARGO_CFG_TARGET_OS").unwrap();
    if os == "android" || os == "ios" {
        link_stockfish(&os);
    }
    tauri_build::build()
}

/// Compiles spike/vendor/Stockfish (sf_19, with its net) and cpp/sfshim.cpp into the library.
fn link_stockfish(os: &str) {
    let arch = env::var("CARGO_CFG_TARGET_ARCH").unwrap();
    assert!(
        arch == "aarch64",
        "Stockfish is built with the aarch64 flags of ARCH=armv8-dotprod only, not for {arch}"
    );
    let src =
        PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../../vendor/Stockfish/src");
    let mut build = cc::Build::new();
    build
        .cpp(true)
        .opt_level(3) // -O3 without -g in a debug build too: cc takes both from the cargo profile
        .debug(false)
        .extra_warnings(false) // no -Wextra: the Makefile's warnings are -Wall -Wcast-qual
        .include(&src) // also where the .incbin of nnue/network.cpp finds the net, as from make's cwd
        .files(SOURCES.iter().map(|file| src.join(file)))
        .file("cpp/sfshim.cpp");
    for flag in FLAGS {
        build.flag(flag);
    }
    if os == "android" {
        build.cpp_link_stdlib("c++_static"); // no libc++_shared.so to ship next to the library
    }
    build.compile("stockfish");
    if os == "android" {
        println!("cargo:rustc-link-lib=c++abi"); // libc++_static.a leaves it out
    }
    println!("cargo:rerun-if-changed=cpp/sfshim.cpp");
}
