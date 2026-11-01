`timescale 1ns / 1ps
// Drives the two-byte command the console sends, and prints the UART text.
// +CASE= is the dataset byte ('0' is 48, 'A' is 65). +TOPO= is 0..83, or 255 for every shape.

module study_top_tb;
  reg clk = 0;
  reg uart_rx = 1;
  wire uart_tx;
  wire [7:0] leds;

  localparam integer CLK_HZ = 921600;
  localparam integer BAUD = 115200;
  localparam integer CLKS = CLK_HZ / BAUD;
  localparam integer STDOUT = 32'h8000_0001;

  study_top #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
    .clk(clk),
    .uart_rx(uart_rx),
    .uart_tx(uart_tx),
    .leds(leds)
  );

  always #4 clk = ~clk;

  integer cycles;
  integer cmd_case;
  integer cmd_topo;
  reg rx_busy;
  reg [15:0] rx_cnt;
  reg [3:0] rx_bit;
  reg [7:0] rx_shift;
  reg saw_z;

  task send_byte;
    input [7:0] data;
    integer i;
    begin
      uart_rx = 1'b0;
      repeat (CLKS) @(posedge clk);
      for (i = 0; i < 8; i = i + 1) begin
        uart_rx = data[i];
        repeat (CLKS) @(posedge clk);
      end
      uart_rx = 1'b1;
      repeat (CLKS * 2) @(posedge clk);
    end
  endtask

  initial begin
    cmd_case = 48;
    cmd_topo = 0;
    $value$plusargs("CASE=%d", cmd_case);
    $value$plusargs("TOPO=%d", cmd_topo);
    rx_busy = 0;
    saw_z = 0;
    cycles = 0;
    repeat (40) @(posedge clk);
    send_byte(8'hFE);
    send_byte(cmd_case[7:0]);
    send_byte(cmd_topo[7:0]);
  end

  always @(posedge clk) begin
    begin
      cycles = cycles + 1;
      if (cycles == 400000000) begin
        $display("TIMEOUT");
        $fflush(STDOUT);
        $finish;
      end
      if (!rx_busy && uart_tx == 1'b0) begin
        rx_busy <= 1'b1;
        rx_cnt <= CLKS + (CLKS / 2) - 2;
        rx_bit <= 4'd0;
      end else if (rx_busy) begin
        if (rx_cnt == 0) begin
          if (rx_bit < 4'd8) begin
            rx_shift[rx_bit] <= uart_tx;
            rx_bit <= rx_bit + 4'd1;
            rx_cnt <= CLKS - 1;
          end else begin
            $fwrite(STDOUT, "%c", rx_shift);
            if (rx_shift == 8'h0A)
              $fflush(STDOUT);
            if (rx_shift == "Z")
              saw_z <= 1'b1;
            if (saw_z && rx_shift == 8'h0A) begin
              $display("DONE cycles=%0d", cycles);
              $fflush(STDOUT);
              $finish;
            end
            rx_busy <= 1'b0;
          end
        end else
          rx_cnt <= rx_cnt - 16'd1;
      end
    end
  end
endmodule
