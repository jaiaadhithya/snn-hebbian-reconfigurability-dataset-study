"""Build the on-chip sample ROM for the ZedBoard reconfiguration study.

The patterns are the dataset. The network that classifies them is the FPGA design.
Full MNIST does not fit in the ZedBoard block RAM, so the large-input case is an
8 by 8 binary digit with the same shape as a downsampled digit: 64 inputs, 10 classes.
"""

from __future__ import annotations

import json
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RTL = os.path.join(ROOT, "rtl")

# Ink lives in pixels 24..63 so a fixed mux wired to pixels 0..7 sees nothing.
DIGITS = {
    0: {24, 25, 30, 31, 32, 39, 40, 47, 48, 55, 56, 57, 62, 63},
    1: {26, 34, 42, 50, 58},
    2: {24, 25, 26, 27, 35, 43, 44, 45, 46, 48, 56, 57, 58, 59},
    3: {24, 25, 26, 27, 35, 42, 43, 44, 51, 56, 57, 58, 59},
    4: {24, 28, 32, 36, 40, 41, 42, 43, 44, 52, 60},
    5: {24, 25, 26, 27, 32, 40, 41, 42, 43, 51, 56, 57, 58, 59},
    6: {27, 34, 40, 41, 42, 43, 48, 51, 56, 57, 58, 59},
    7: {24, 25, 26, 27, 35, 43, 51, 59},
    8: {24, 25, 26, 27, 32, 35, 40, 41, 42, 43, 48, 51, 56, 57, 58, 59},
    9: {24, 25, 26, 27, 32, 35, 40, 41, 42, 43, 51, 59},
}


def pack(bits) -> int:
    value = 0
    for bit in bits:
        value |= 1 << bit
    return value


def one_hot(index: int) -> int:
    return 1 << index


def add_case(cases, name, layers, n_in, n_class, samples, note):
    cases.append({
        "name": name,
        "layers": layers,
        "n_in": n_in,
        "n_class": n_class,
        "note": note,
        "samples": samples,
    })


def small_pattern(index: int) -> int:
    return one_hot(index)


def build() -> list[dict]:
    rng = np.random.default_rng(1)
    cases: list[dict] = []

    aligned = []
    for index in range(4):
        for _ in range(4):
            aligned.append((small_pattern(index), index, 1, 0))
        aligned.append((small_pattern(index), index, 0, 1))
    add_case(
        cases, "aligned_small", 1, 4, 4, aligned,
        "Four one-hot patterns. The label matches the reset wiring.",
    )

    permuted = []
    for index in range(4):
        label = (index + 1) % 4
        for _ in range(4):
            permuted.append((small_pattern(index), label, 1, 0))
        permuted.append((small_pattern(index), label, 0, 1))
    add_case(
        cases, "permuted_small", 1, 4, 4, permuted,
        "Same four patterns. The label is rotated, so the reset wiring is wrong.",
    )

    switched = []
    for _ in range(4):
        for index in range(4):
            switched.append((small_pattern(index), index, 1, 0))
    for _ in range(8):
        for index in range(4):
            switched.append((small_pattern(index), (index + 1) % 4, 1, 1))
    for index in range(4):
        switched.append((small_pattern(index), (index + 1) % 4, 0, 2))
    for index in range(4):
        switched.append((small_pattern(index), index, 0, 3))
    add_case(
        cases, "switched_small", 1, 4, 4, switched,
        "Train the aligned labels, then train the rotated labels. Test both regimes.",
    )

    noisy = []
    for index in range(4):
        for rep in range(4):
            bits = small_pattern(index) | one_hot((index + 1 + rep) % 4)
            noisy.append((bits, index, 1, 0))
        noisy.append((small_pattern(index) | one_hot((index + 1) % 4), index, 0, 1))
    add_case(
        cases, "noisy_small", 1, 4, 4, noisy,
        "Aligned labels, plus one extra active input that is not the class.",
    )

    random_case = []
    for _ in range(16):
        bits = int(rng.integers(1, 16))
        label = int(rng.integers(0, 4))
        random_case.append((bits, label, 1, 0))
    for _ in range(8):
        bits = int(rng.integers(1, 16))
        label = int(rng.integers(0, 4))
        random_case.append((bits, label, 0, 1))
    add_case(
        cases, "random_small", 1, 4, 4, random_case,
        "Random 4-bit patterns and random labels. There is no class structure.",
    )

    distinct = []
    for digit in range(10):
        # Three private pixels. None sit in 0..7, and no two classes share a pixel.
        pixels = {16 + digit, 32 + digit, 48 + digit}
        for _ in range(4):
            distinct.append((pack(pixels), digit, 1, 0))
        distinct.append((pack(pixels), digit, 0, 1))
    add_case(
        cases, "distinct64", 1, 64, 10, distinct,
        "Ten classes on 64 inputs. Each class owns three pixels and shares none. The pixels sit past the eight inputs a reset mux can see.",
    )

    distinct_deep = []
    for digit in range(10):
        pixels = {16 + digit, 32 + digit, 48 + digit}
        for _ in range(6):
            distinct_deep.append((pack(pixels), digit, 1, 0))
        distinct_deep.append((pack(pixels), digit, 0, 1))
    add_case(
        cases, "distinct64_3layer", 3, 64, 10, distinct_deep,
        "The same disjoint classes through three mux layers. A recruited spike has to cross each layer.",
    )

    glyphs = []
    for digit, pixels in DIGITS.items():
        for _ in range(4):
            glyphs.append((pack(pixels), digit, 1, 0))
        glyphs.append((pack(pixels), digit, 0, 1))
    add_case(
        cases, "glyphs64", 1, 64, 10, glyphs,
        "Ten 8x8 digit drawings. Strokes overlap, so the earliest ink pixel is not private to one class.",
    )

    noisy_glyphs = []
    for digit, pixels in DIGITS.items():
        for _rep in range(4):
            noise = {int(rng.integers(0, 16)) for _ in range(3)}
            noisy_glyphs.append((pack(set(pixels) | noise), digit, 1, 0))
        noise = {int(rng.integers(0, 16)) for _ in range(3)}
        noisy_glyphs.append((pack(set(pixels) | noise), digit, 0, 1))
    add_case(
        cases, "glyphs64_noise", 1, 64, 10, noisy_glyphs,
        "Digit drawings plus three random pixels in addresses 0..15. Those addresses are exactly where a reset mux looks, and they change between samples.",
    )

    deep_perm = []
    for index in range(4):
        label = (index + 2) % 4
        for _ in range(6):
            deep_perm.append((small_pattern(index), label, 1, 0))
        deep_perm.append((small_pattern(index), label, 0, 1))
    add_case(
        cases, "permuted_3layer", 3, 4, 4, deep_perm,
        "Rotated labels again, with three hidden mux layers instead of one.",
    )
    return cases


def write_rom(cases: list[dict]) -> None:
    samples = []
    metas = []
    cursor = 0
    for case in cases:
        metas.append({
            "layers": case["layers"],
            "n_in": case["n_in"],
            "n_class": case["n_class"],
            "start": cursor,
            "n_train": sum(1 for item in case["samples"] if item[2]),
            "n_test": sum(1 for item in case["samples"] if not item[2]),
        })
        for bits, label, train, group in case["samples"]:
            word = (bits & ((1 << 64) - 1))
            word |= (label & 0xF) << 64
            word |= (train & 1) << 68
            word |= (group & 0x3) << 69
            samples.append(word)
            cursor += 1

    lines = [
        "`timescale 1ns / 1ps",
        "// Generated by tools/gen_datasets.py. Do not edit by hand.",
        "",
        "module study_rom (",
        "  input  wire [3:0] case_i,",
        "  input  wire [7:0] sample_i,",
        "  output reg  [63:0] bits,",
        "  output reg  [3:0]  label,",
        "  output reg         train,",
        "  output reg  [1:0]  group,",
        "  output reg  [3:0]  n_layers,",
        "  output reg  [6:0]  n_in,",
        "  output reg  [3:0]  n_class,",
        "  output reg  [7:0]  n_train,",
        "  output reg  [7:0]  n_test,",
        "  output reg  [7:0]  n_cases",
        ");",
        "",
        f"  localparam integer N_SAMPLES = {len(samples)};",
        f"  localparam integer N_CASES = {len(cases)};",
        "  reg [70:0] mem [0:N_SAMPLES-1];",
        "  reg [47:0] meta [0:N_CASES-1];",
        "  wire [11:0] addr = meta[case_i][27:16] + sample_i;",
        "",
        "  initial begin",
    ]
    for i, word in enumerate(samples):
        lines.append(f"    mem[{i}] = 71'h{word:018x};")
    for i, meta in enumerate(metas):
        packed = (
            (meta["n_test"] & 0xFF)
            | ((meta["n_train"] & 0xFF) << 8)
            | ((meta["start"] & 0xFFF) << 16)
            | ((meta["n_class"] & 0xF) << 28)
            | ((meta["n_in"] & 0x7F) << 32)
            | ((meta["layers"] & 0xF) << 39)
        )
        lines.append(f"    meta[{i}] = 48'h{packed:012x};")
    lines += [
        "  end",
        "",
        "  always @(*) begin",
        "    n_cases = N_CASES[7:0];",
        "    n_layers = meta[case_i][42:39];",
        "    n_in     = meta[case_i][38:32];",
        "    n_class  = meta[case_i][31:28];",
        "    n_train  = meta[case_i][15:8];",
        "    n_test   = meta[case_i][7:0];",
        "    bits  = mem[addr][63:0];",
        "    label = mem[addr][67:64];",
        "    train = mem[addr][68];",
        "    group = mem[addr][70:69];",
        "  end",
        "endmodule",
        "",
    ]
    os.makedirs(RTL, exist_ok=True)
    with open(os.path.join(RTL, "study_rom.v"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))

    public = []
    for index, case in enumerate(cases):
        public.append({
            "id": index,
            "name": case["name"],
            "layers": case["layers"],
            "n_in": case["n_in"],
            "n_class": case["n_class"],
            "note": case["note"],
            "n_train": sum(1 for item in case["samples"] if item[2]),
            "n_test": sum(1 for item in case["samples"] if not item[2]),
        })
    with open(os.path.join(ROOT, "cases.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(public, handle, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    write_rom(build())
    print(os.path.join(RTL, "study_rom.v"))
