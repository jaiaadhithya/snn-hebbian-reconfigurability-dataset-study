# Run the sweep on the ZedBoard

The bitstream holds the network, the 84 shapes, and the datasets. The lab PC sends two bytes, the dataset and the topology, then records what the board sends back. There is no ADC and no button. The samples are already in `rtl/study_rom.v`.

The lab PC does not need Icarus. Python only needs `pyserial`. Vivado builds the bitstream and programs the programmable logic. The console talks to a USB-serial adapter.

The USB-UART jack on the ZedBoard goes to the ARM. This design does not boot the ARM, so that jack stays silent. Use a separate 3.3 V USB-serial adapter on PMOD JA.

An older bitstream that starts by itself will not answer this console. Build `study_top.bit` from this folder before you program the board.

## What you need

- ZedBoard, part XC7Z020-clg484-1
- Vivado with the Zynq-7000 device XC7Z020
- A 3.3 V USB-serial adapter. Do not use a 5 V TTL adapter
- Python 3

```bat
pip install pyserial
```

Clone this repository and open a shell in the folder that contains `vivado_create.tcl`.

## 1. Power and jumpers

1. Set **JP7** to **JTAG**.
2. Set **JP8** to **USB**.
3. Plug the PROG USB cable into the lab PC.
4. Power the board.

## 2. Serial wire

PMOD JA pin 1 is the pin marked 1 on the silkscreen. Pin 2 is the next pin in that row. Ground is a pin marked GND, pin 5 or pin 11.

| ZedBoard | USB-serial adapter |
| --- | --- |
| JA pin 1, FPGA ball Y11 | RX |
| JA pin 2, FPGA ball AA11 | TX |
| JA GND, pin 5 or pin 11 | GND |

Set the adapter to 3.3 V. The link is 115200 baud, 8 data bits, no parity, 1 stop bit.

JA pin 1 carries scores from the FPGA to the PC. JA pin 2 carries the command from the PC to the FPGA. Both wires are required. Share one ground. Do not use the onboard UART jack.

## 3. Create the Vivado project

In a Vivado Tcl shell, from this folder:

```bat
python tools\gen_datasets.py
vivado -mode batch -source vivado_create.tcl
```

`python tools\gen_datasets.py` rewrites `rtl/study_rom.v` and `cases.json`. Run it before the Vivado script so the ROM in the project is current.

`vivado_create.tcl` creates the project for part `xc7z020clg484-1`. It adds `study_top.v`, `snn_engine.v`, `study_rom.v`, `uart_tx.v`, `uart_rx.v`, and `constraints/zedboard_study.xdc`. The top module is `study_top`.

Close an older project first. The script overwrites `fpga_reconfig_study/fpga_reconfig_study.xpr`.

## 4. Synthesize and write the bitstream

In the Vivado Tcl console:

```tcl
open_project fpga_reconfig_study/fpga_reconfig_study.xpr
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
```

Wait until both runs report a successful finish. The bitstream is:

`fpga_reconfig_study/fpga_reconfig_study.runs/impl_1/study_top.bit`

If `synth_1` fails, open the run log from the Vivado console before launching implementation. Do not program an older `study_top.bit`.

## 5. Program the FPGA

1. Slide **SW0 up**. Up holds the design in reset.
2. In Vivado: **Flow → Open Hardware Manager → Open Target → Auto Connect**.
3. Select the Zynq device and **Program Device**.
4. Choose `study_top.bit`.
5. Program the programmable logic only. Do not add a boot image, an FSBL, or a Linux image.
6. Leave SW0 up until programming finishes.

## 6. Release reset

Slide **SW0 down**.

The board waits. It does not start a sweep on its own. LD7 stays off until the console sends a command.

## 7. Find the adapter COM port

Open Device Manager and expand **Ports (COM & LPT)**. Use the port that belongs to the USB-serial adapter, for example `COM5`.

Do not use the Digilent or Cypress port from the PROG cable. Close any serial terminal that has the adapter port open.

## 8. Run the console

```bat
python tools\study_console.py
```

On a lab PC without Icarus, **Port** starts as `COM5`. Change it to the adapter port from Device Manager. Do not type `Icarus`.

1. Choose one dataset, or **All datasets**.
2. Choose one topology, or **All topologies**.
3. Set **Port** to the adapter COM port.
4. Press **Run**.

The console sends the dataset byte and then the topology byte. **All topologies** walks 84 shapes. Each live layer is 2, 4, 6, or 8 neurons, chosen on its own. One layer has 4 shapes, two layers have 16, and three layers have 64. A single topology runs that shape with learning on, then the same shape frozen.

The diagram draws the shape that is running. Neuron 0 of every column is on the same row. A blue arrow is the one mux into a hidden neuron. A green arrow is its strong output class. Under the diagram, each test input appears with its label and the class the network chose. The score shows neurons, spikes, and energy. Energy is one mux event per hidden spike, plus one addition into each class for every last-layer spike.

**Topology** switches among finished shapes. **Results** names the learned shape with the best accuracy, and a cheaper shape when another learned shape ties that accuracy at lower energy.

LD7 turns on when the sweep starts and turns off when the board is waiting again. Leave SW0 down and press Run to start another selection.

## 9. Files

Each Run creates `results\sweeps\<date>_<time>\`. Lines are written as they arrive.

| File | What it holds |
| --- | --- |
| `uart_log.txt` | Every line from the board |
| `metrics.csv` | Score, neurons, spikes, and energy |
| `trials.csv` | Label and prediction for every test sample |
| `mux.csv` | Where each mux pointed at the end of the pass |
| `rasters.csv` | Spikes at every step |
| `summary.md` | The scores in a table |

Git ignores `results/`. The folder is still on disk. Open it in File Explorer.

## 10. LEDs

| LED | Meaning |
| --- | --- |
| LD0–LD3 | Dataset number, in binary |
| LD4 | On during a learned pass. Off during a frozen pass |
| LD5 | The network is busy on a sample |
| LD6 | A UART byte is in flight |
| LD7 | On while a sweep is running. Off while the board is waiting |

## If nothing arrives

The status line says so after about 20 seconds.

1. SW0 must be down. Up is reset, and a command sent during reset is ignored.
2. JA pin 1 must go to the adapter RX, and JA pin 2 to the adapter TX.
3. **Port** must be the adapter COM port, not the PROG cable, and not the word `Icarus`.
4. The programmed file must be `study_top.bit` from this build.
5. Slide SW0 up, then down, and press Run again.

## Simulate on a PC that has Icarus

This step is optional. The lab ZedBoard run does not use it.

If `iverilog` and `vvp` are installed, the console starts with **Port** set to `Icarus`. Press **Run** and the same two-byte command is simulated on that PC. One small dataset and one topology is the short check. All topologies takes much longer. Type a COM name instead when a board is connected.
