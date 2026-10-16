# Run the study on the ZedBoard

The network runs in the programmable logic. The laptop only builds the bitstream and records the UART stream. The patterns are already inside `rtl/study_rom.v`, so the board does not need an ADC, buttons, or a host feeding samples.

The USB-UART jack on the ZedBoard goes to the ARM chip. This design does not boot the ARM, so that jack stays silent. The scores come out of PMOD JA pin 1.

## What you need

- ZedBoard (XC7Z020-clg484-1)
- Vivado with Zynq-7000 support
- A 3.3 V USB-serial adapter (FTDI or similar). Do not use a 5 V TTL adapter.
- Python 3

```bat
pip install pyserial numpy matplotlib
```

## 1. Jumpers and power

1. Set **JP7** to **JTAG**.
2. Set **JP8** to **USB**.
3. Plug the PROG USB cable into the PC.
4. Power the board.

## 2. Serial wire

| ZedBoard | USB-serial adapter |
| --- | --- |
| JA pin 1 (FPGA ball Y11), the pin marked 1 on the JA connector | RX |
| JA pin 5 or pin 11 (GND) | GND |

Leave the adapter’s TX unconnected. Set the adapter to 3.3 V. The FPGA transmits 115200 baud, 8 data bits, no parity, 1 stop bit.

Share one ground. The onboard UART jack is not this port.

## 3. Build the bitstream

Open a Vivado Tcl shell, then:

```bat
cd C:\Users\jaiaa\OneDrive\Desktop\stuff\study\snn-hebbian-reconfigurability-dataset-study
python tools\gen_datasets.py
vivado -mode batch -source vivado_create.tcl
```

In the Vivado Tcl console:

```tcl
open_project fpga_reconfig_study/fpga_reconfig_study.xpr
launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
```

The bitstream is:

`fpga_reconfig_study/fpga_reconfig_study.runs/impl_1/study_top.bit`

Synthesis and implementation run on the PC. After the `.bit` file exists, the study itself runs on the FPGA.

## 4. Hold the FPGA in reset, then program it

1. Slide **SW0 up**. Up holds the design in reset, so it does not start transmitting before the laptop is listening.
2. In Vivado: **Flow → Open Hardware Manager → Open Target → Auto Connect**.
3. **Program Device** and choose `study_top.bit`.
4. Program the programmable logic only. There is no FSBL and no Linux image.
5. Leave SW0 up.

## 5. Record, then release reset

Find the adapter’s COM port in Device Manager (not the Digilent or Cypress port that belongs to the PROG cable). Then, with SW0 still up:

```bat
cd C:\Users\jaiaa\OneDrive\Desktop\stuff\study\snn-hebbian-reconfigurability-dataset-study
python tools\collect_and_plot.py --port COM5
```

Replace `COM5` with the adapter’s port. When the script prints `Listening`, slide **SW0 down**.

The FPGA then runs every case twice, frozen and then reconfigurable, and sends the scores out JA pin 1. The script prints each line as it arrives. When the final `Z` line shows up, the script writes:

- `results/uart_log.txt` — the raw text from the board
- `results/metrics.csv`
- `results/summary.png`
- `results/raster_<case>.png`
- `results/STUDY_RESULTS.md`

That capture replaces the earlier simulation log in `results/`.

The transmission is a few seconds long. If SW0 was already down when you started the script, the run already finished and the bytes are gone. Slide SW0 up, start the script again, then slide SW0 down.

## 6. What the LEDs mean

| LED | Meaning |
| --- | --- |
| LD0–LD3 | Case number, in binary. It counts from 0 through 9. |
| LD4 | On during the reconfigurable pass of the current case. Off during the frozen pass. |
| LD5 | The network is busy on a sample. |
| LD6 | A UART byte is in flight. |
| LD7 | On when every case has been sent. |

LD7 staying on means the FPGA finished. The laptop still has to have been listening from the moment SW0 went down.

To run it again: SW0 up, start the script, SW0 down.
