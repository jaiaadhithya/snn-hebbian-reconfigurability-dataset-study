# Create the Vivado project for the reconfiguration study.
#   vivado -mode batch -source vivado_create.tcl

set script_dir [file normalize [file dirname [info script]]]
set proj_name  fpga_reconfig_study
set part       xc7z020clg484-1

set rtl_files [list \
  [file join $script_dir rtl study_top.v] \
  [file join $script_dir rtl snn_engine.v] \
  [file join $script_dir rtl study_rom.v] \
  [file join $script_dir rtl uart_tx.v] \
]

set xdc_file [file join $script_dir constraints zedboard_study.xdc]

create_project $proj_name [file join $script_dir $proj_name] -part $part -force
set_property target_language Verilog [current_project]
add_files -norecurse $rtl_files
add_files -fileset constrs_1 -norecurse $xdc_file
set_property top study_top [current_fileset]
update_compile_order -fileset sources_1

puts "Project created: [file join $script_dir $proj_name]"
puts "Next: open_project [file join $script_dir $proj_name $proj_name.xpr]"
puts "      launch_runs synth_1 -jobs 4"
puts "      wait_on_run synth_1"
puts "      launch_runs impl_1 -to_step write_bitstream -jobs 4"
puts "      wait_on_run impl_1"
