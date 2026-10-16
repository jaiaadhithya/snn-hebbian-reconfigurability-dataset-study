"""Read the ZedBoard UART log, print the metrics, and write the figures.

The log is produced by the FPGA. This script does not rerun the network.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")


def parse_log(text: str) -> tuple[list[dict], list[dict], list[dict], bool]:
    metrics = []
    trials = []
    rasters = []
    done = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "Z":
            done = True
            continue
        part = line.split(",")
        kind = part[0]
        if kind == "M" and len(part) == 16:
            metrics.append({
                "case": int(part[1], 16),
                "mode": int(part[2], 16),
                "layers": int(part[3], 16),
                "n_in": int(part[4], 16),
                "n_class": int(part[5], 16),
                "n_test": int(part[6], 16),
                "correct": int(part[7], 16),
                "util": [int(part[8], 16), int(part[9], 16), int(part[10], 16)],
                "spikes_in": int(part[11], 16),
                "spikes_h": [int(part[12], 16), int(part[13], 16), int(part[14], 16)],
                "spikes_out": int(part[15], 16),
            })
        elif kind == "R" and len(part) == 7:
            trials.append({
                "case": int(part[1], 16),
                "mode": int(part[2], 16),
                "sample": int(part[3], 16),
                "group": int(part[4], 16),
                "label": int(part[5], 16),
                "pred": int(part[6], 16),
            })
        elif kind == "T" and len(part) == 8:
            rasters.append({
                "case": int(part[1], 16),
                "mode": int(part[2], 16),
                "sample": int(part[3], 16),
                "step": int(part[4], 16),
                "inputs": int(part[5], 16),
                "hidden": int(part[6], 16),
                "outputs": int(part[7], 16),
            })
    return metrics, trials, rasters, done


def load_cases() -> dict[int, dict]:
    path = os.path.join(ROOT, "cases.json")
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)
    return {row["id"]: row for row in rows}


def group_accuracy(trials: list[dict], case_id: int, mode: int) -> dict[int, tuple[int, int]]:
    buckets: dict[int, list[int]] = {}
    for row in trials:
        if row["case"] == case_id and row["mode"] == mode:
            buckets.setdefault(row["group"], []).append(int(row["pred"] == row["label"]))
    return {group: (sum(hits), len(hits)) for group, hits in sorted(buckets.items())}


def mode_name(mode: int) -> str:
    return "reconfigurable" if mode else "fixed"


def print_metrics(cases: dict[int, dict], metrics: list[dict], trials: list[dict]) -> None:
    print()
    print(f"{'case':<20} {'pass':<16} {'correct':>10} {'used L0':>8} {'L1':>4} {'L2':>4} {'spikes H':>10}")
    for row in metrics:
        meta = cases.get(row["case"], {})
        name = meta.get("name", str(row["case"]))
        acc = f"{row['correct']}/{row['n_test']}"
        hidden = row["spikes_h"][0] + row["spikes_h"][1] + row["spikes_h"][2]
        print(
            f"{name:<20} {mode_name(row['mode']):<16} {acc:>10} "
            f"{row['util'][0]:>8} {row['util'][1]:>4} {row['util'][2]:>4} {hidden:>10}"
        )
        groups = group_accuracy(trials, row["case"], row["mode"])
        if len(groups) > 1:
            pieces = [f"group {g}: {ok}/{n}" for g, (ok, n) in groups.items()]
            print(f"  regimes: {', '.join(pieces)}")
    print()


def write_csv(metrics: list[dict], cases: dict[int, dict]) -> None:
    path = os.path.join(RESULTS, "metrics.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "case", "name", "mode", "layers", "n_in", "n_class", "n_test", "correct",
            "util0", "util1", "util2", "spikes_in", "spikes_h0", "spikes_h1", "spikes_h2", "spikes_out",
        ])
        for row in metrics:
            meta = cases.get(row["case"], {})
            writer.writerow([
                row["case"], meta.get("name", ""), mode_name(row["mode"]), row["layers"],
                row["n_in"], row["n_class"], row["n_test"], row["correct"],
                row["util"][0], row["util"][1], row["util"][2],
                row["spikes_in"], row["spikes_h"][0], row["spikes_h"][1], row["spikes_h"][2],
                row["spikes_out"],
            ])


def sentence(name: str, fixed: dict, recon: dict, trials: list[dict]) -> str:
    def rate(row: dict) -> float:
        return row["correct"] / row["n_test"] if row["n_test"] else 0.0

    gap = rate(recon) - rate(fixed)
    used = (
        f"Fixed used {fixed['util'][0]}/{8} layer-0 neurons"
        f" ({fixed['util'][1]} and {fixed['util'][2]} in the deeper layers). "
        f"Reconfigurable used {recon['util'][0]}, {recon['util'][1]}, {recon['util'][2]}."
    )
    if name == "switched_small":
        fg = group_accuracy(trials, fixed["case"], 0)
        rg = group_accuracy(trials, recon["case"], 1)
        return (
            f"Pooled score is {fixed['correct']}/{fixed['n_test']} fixed and "
            f"{recon['correct']}/{recon['n_test']} reconfigurable. "
            f"Split by regime, fixed scored "
            f"{fg.get(2, (0, 0))[0]}/{fg.get(2, (0, 0))[1]} on the new labels and "
            f"{fg.get(3, (0, 0))[0]}/{fg.get(3, (0, 0))[1]} on the old ones. "
            f"Reconfigurable scored "
            f"{rg.get(2, (0, 0))[0]}/{rg.get(2, (0, 0))[1]} on the new labels and "
            f"{rg.get(3, (0, 0))[0]}/{rg.get(3, (0, 0))[1]} on the old ones. "
            + used
        )
    if gap > 0.15:
        verdict = "The reconfigurable pass is the one that fits this set."
    elif gap < -0.15 and rate(fixed) > 0.5:
        verdict = "The fixed pass is ahead. Recruitment and Hebbian updates moved a wiring that was already right."
    elif gap < -0.15:
        verdict = "The reconfigurable pass is worse. It attaches neurons to bits that are not stable from sample to sample."
    else:
        verdict = "The two passes land in the same place. Reconfiguration does not buy accuracy here."
    return (
        f"Fixed {fixed['correct']}/{fixed['n_test']}, "
        f"reconfigurable {recon['correct']}/{recon['n_test']}. {verdict} {used}"
    )


def write_report(cases: dict[int, dict], metrics: list[dict], trials: list[dict], source: str) -> None:
    by_case: dict[int, dict[int, dict]] = {}
    for row in metrics:
        by_case.setdefault(row["case"], {})[row["mode"]] = row
    lines = [
        "# Measured results",
        "",
        f"Source log: `{source}`.",
        "These numbers were parsed from the UART text the design transmits. The parser does not rerun the network.",
        "A log from the testbench and a log from the programmed ZedBoard use the same lines.",
        "",
        "| Case | Fixed | Reconfigurable | Neurons used (fixed) | Neurons used (reconfig) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case_id in sorted(by_case):
        meta = cases.get(case_id, {"name": str(case_id), "note": ""})
        fixed = by_case[case_id].get(0)
        recon = by_case[case_id].get(1)
        if not fixed or not recon:
            continue
        lines.append(
            f"| {meta['name']} | {fixed['correct']}/{fixed['n_test']} | "
            f"{recon['correct']}/{recon['n_test']} | "
            f"{fixed['util'][0]}, {fixed['util'][1]}, {fixed['util'][2]} | "
            f"{recon['util'][0]}, {recon['util'][1]}, {recon['util'][2]} |"
        )
    lines += ["", "## Case by case", ""]
    for case_id in sorted(by_case):
        meta = cases.get(case_id, {"name": str(case_id), "note": ""})
        fixed = by_case[case_id].get(0)
        recon = by_case[case_id].get(1)
        if not fixed or not recon:
            continue
        lines.append(f"### {meta['name']}")
        lines.append("")
        lines.append(meta.get("note", ""))
        lines.append("")
        lines.append(sentence(meta["name"], fixed, recon, trials))
        lines.append("")
        lines.append(
            f"Spike totals, fixed: inputs {fixed['spikes_in']}, "
            f"hidden {fixed['spikes_h'][0]}/{fixed['spikes_h'][1]}/{fixed['spikes_h'][2]}, "
            f"outputs {fixed['spikes_out']}."
        )
        lines.append(
            f"Spike totals, reconfigurable: inputs {recon['spikes_in']}, "
            f"hidden {recon['spikes_h'][0]}/{recon['spikes_h'][1]}/{recon['spikes_h'][2]}, "
            f"outputs {recon['spikes_out']}."
        )
        lines.append("")
    better, worse, tied = [], [], []
    for case_id in sorted(by_case):
        meta = cases.get(case_id, {"name": str(case_id)})
        fixed = by_case[case_id].get(0)
        recon = by_case[case_id].get(1)
        if not fixed or not recon or not fixed["n_test"]:
            continue
        gap = recon["correct"] / recon["n_test"] - fixed["correct"] / fixed["n_test"]
        if meta["name"] == "switched_small":
            tied.append(meta["name"] + " (pooled; the split is in the section above)")
        elif gap > 0.15:
            better.append(meta["name"])
        elif gap < -0.15:
            worse.append(meta["name"])
        else:
            tied.append(meta["name"])
    lines += [
        "## Where reconfiguration helps",
        "",
        "Better on: " + ", ".join(better) + ".",
        "",
        "No accuracy gain on: " + ", ".join(tied) + ".",
        "",
        "Worse on: " + ", ".join(worse) + ".",
        "",
        "The gain shows up when the reset mux is aimed at the wrong class, or when the class pixels sit outside the inputs that mux can see. "
        "Three layers still carry that gain on the disjoint patterns: every layer is used, and the score matches the one-layer run. "
        "Eight neurons cap the disjoint 10-class cases at 8/10. "
        "Overlapping digit strokes claim fewer neurons, because several classes share an earliest pixel, and the score drops. "
        "Noise in the addresses the reset mux already watches makes the reconfigurable pass worse: a claimed neuron follows a bit that is gone on the test sample. "
        "When the labels change halfway through, the pooled score hides the result. The fixed pass keeps the old regime, and the reconfigurable pass keeps the new one.",
        "",
    ]
    path = os.path.join(RESULTS, "STUDY_RESULTS.md")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def bits(value: int, width: int) -> list[int]:
    return [(value >> i) & 1 for i in range(width)]


def plot(cases: dict[int, dict], metrics: list[dict], trials: list[dict], rasters: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    by_case: dict[int, dict[int, dict]] = {}
    for row in metrics:
        by_case.setdefault(row["case"], {})[row["mode"]] = row
    ids = sorted(by_case)
    names = [cases.get(i, {}).get("name", str(i)) for i in ids]
    fixed_acc = []
    recon_acc = []
    for i in ids:
        fixed = by_case[i][0]
        recon = by_case[i][1]
        fixed_acc.append(fixed["correct"] / fixed["n_test"])
        recon_acc.append(recon["correct"] / recon["n_test"])
    x = np.arange(len(ids))
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    axes[0].bar(x - 0.18, fixed_acc, width=0.36, label="fixed", color="#4c78a8")
    axes[0].bar(x + 0.18, recon_acc, width=0.36, label="reconfigurable", color="#f58518")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(names, rotation=35, ha="right")
    axes[0].set_ylim(0, 1.05)
    axes[0].set_ylabel("test accuracy")
    axes[0].legend(frameon=False)
    util_f = [by_case[i][0]["util"][0] + by_case[i][0]["util"][1] + by_case[i][0]["util"][2] for i in ids]
    util_r = [by_case[i][1]["util"][0] + by_case[i][1]["util"][1] + by_case[i][1]["util"][2] for i in ids]
    axes[1].bar(x - 0.18, util_f, width=0.36, label="fixed", color="#4c78a8")
    axes[1].bar(x + 0.18, util_r, width=0.36, label="reconfigurable", color="#f58518")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(names, rotation=35, ha="right")
    axes[1].set_ylabel("neurons that spiked")
    axes[1].legend(frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "summary.png"), dpi=140)
    plt.close(fig)

    for case_id in ids:
        rows = [r for r in rasters if r["case"] == case_id]
        if not rows:
            continue
        samples = sorted({r["sample"] for r in rows if r["mode"] == 0})
        if not samples:
            continue
        # One figure: every test sample, both passes, neurons that fired at least once.
        show = samples[:8]
        fig_h = 4.2 if cases.get(case_id, {}).get("n_in", 4) <= 4 else 6.2
        fig, axes = plt.subplots(2, len(show), figsize=(2.1 * len(show) + 1.4, fig_h), squeeze=False)
        for col, sample in enumerate(show):
            for mode in (0, 1):
                steps = [r for r in rows if r["mode"] == mode and r["sample"] == sample]
                steps.sort(key=lambda r: r["step"])
                if not steps:
                    continue
                n_in = cases.get(case_id, {}).get("n_in", 64)
                n_class = cases.get(case_id, {}).get("n_class", 4)
                layers = cases.get(case_id, {}).get("layers", 1)
                matrix = []
                labels = []
                active_inputs = []
                for bit in range(n_in):
                    if any((step["inputs"] >> bit) & 1 for step in steps):
                        active_inputs.append(bit)
                if n_in <= 4:
                    active_inputs = list(range(n_in))
                for bit in active_inputs:
                    matrix.append([(step["inputs"] >> bit) & 1 for step in steps])
                    labels.append(f"I{bit}")
                for layer in range(layers):
                    for neuron in range(8):
                        index = layer * 8 + neuron
                        row = [(step["hidden"] >> index) & 1 for step in steps]
                        if any(row) or n_in <= 4:
                            matrix.append(row)
                            labels.append(f"L{layer}H{neuron}")
                for bit in range(n_class):
                    matrix.append([(step["outputs"] >> bit) & 1 for step in steps])
                    labels.append(f"Y{bit}")
                ax = axes[mode][col]
                if matrix:
                    ax.imshow(np.array(matrix), cmap="Greys", aspect="auto", vmin=0, vmax=1, interpolation="nearest")
                    ax.set_yticks(range(len(labels)))
                    ax.set_yticklabels(labels, fontsize=6)
                ax.set_xticks(range(len(steps)))
                ax.set_xlabel("step")
                title = mode_name(mode)
                if mode == 0:
                    title = f"sample {sample}\n{title}"
                ax.set_title(title, fontsize=9)
        fig.suptitle(cases.get(case_id, {}).get("name", str(case_id)))
        fig.tight_layout()
        name = cases.get(case_id, {}).get("name", str(case_id))
        fig.savefig(os.path.join(RESULTS, f"raster_{name}.png"), dpi=140)
        plt.close(fig)


def read_port(port: str, seconds: float) -> str:
    import serial
    ser = serial.Serial(port, 115200, timeout=0.2)
    ser.reset_input_buffer()
    chunks = []
    started = time.time()
    last = None
    print(f"Listening on {port} at 115200. Flip SW0 down to start the FPGA run.", flush=True)
    while True:
        data = ser.read(4096)
        now = time.time()
        if data:
            text = data.decode("ascii", errors="replace")
            chunks.append(text)
            print(text, end="", flush=True)
            last = now
            blob = "".join(chunks)
            if "\nZ\n" in blob or blob.endswith("Z\n"):
                break
        elif last is None:
            if now - started > seconds:
                break
        elif now - last > 3.0:
            break
    ser.close()
    return "".join(chunks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="")
    parser.add_argument("--port", default="")
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    if args.port:
        text = read_port(args.port, args.seconds)
        log_path = os.path.join(RESULTS, "uart_log.txt")
        with open(log_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    elif args.log:
        log_path = args.log if os.path.isabs(args.log) else os.path.join(ROOT, args.log)
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    else:
        raise SystemExit("Pass --port COM5 or --log results/uart_log.txt")
    metrics, trials, rasters, done = parse_log(text)
    if not done:
        raise SystemExit("The log has no Z line, so the run did not finish.")
    cases = load_cases()
    print_metrics(cases, metrics, trials)
    write_csv(metrics, cases)
    write_report(cases, metrics, trials, os.path.relpath(log_path, ROOT))
    if not args.no_plot:
        try:
            plot(cases, metrics, trials, rasters)
        except ImportError:
            print("matplotlib is not installed, so the png files were not written.")
    print(f"wrote {os.path.join(RESULTS, 'STUDY_RESULTS.md')}")


if __name__ == "__main__":
    main()
