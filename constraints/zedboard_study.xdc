# ZedBoard XC7Z020-clg484-1. Samples live in the bitstream.
# PMOD JA pin 1 (Y11) is the only data path back to the laptop.

set_property PACKAGE_PIN Y9 [get_ports clk]
set_property IOSTANDARD LVCMOS33 [get_ports clk]
create_clock -period 8.000 -name sys_clk [get_ports clk]

# SW0 up = reset. The study restarts from case 0.
set_property PACKAGE_PIN F22 [get_ports rst]
set_property IOSTANDARD LVCMOS18 [get_ports rst]

set_property PACKAGE_PIN T22 [get_ports {leds[0]}]
set_property PACKAGE_PIN T21 [get_ports {leds[1]}]
set_property PACKAGE_PIN U22 [get_ports {leds[2]}]
set_property PACKAGE_PIN U21 [get_ports {leds[3]}]
set_property PACKAGE_PIN V22 [get_ports {leds[4]}]
set_property PACKAGE_PIN W22 [get_ports {leds[5]}]
set_property PACKAGE_PIN U19 [get_ports {leds[6]}]
set_property PACKAGE_PIN U14 [get_ports {leds[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {leds[*]}]

# PMOD JA1 — UART TX into a 3.3 V USB-serial adapter RX. GND on JA pin 5 or 11.
set_property PACKAGE_PIN Y11 [get_ports uart_tx]
set_property IOSTANDARD LVCMOS33 [get_ports uart_tx]
