`timescale 1ns / 1ps
// Decodes the study UART and writes the same text the laptop will see.

module study_top_tb;
  reg clk = 0;
  reg rst = 1;
  wire uart_tx;
  wire [7:0] leds;

  localparam integer CLK_HZ = 460800;
  localparam integer BAUD = 115200;
  localparam integer CLKS = CLK_HZ / BAUD;

  study_top #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
    .clk(clk),
    .rst(rst),
    .uart_tx(uart_tx),
    .leds(leds)
  );

  always #4 clk = ~clk;

  integer fd;
  integer cycles;
  reg rx_busy;
  reg [15:0] rx_cnt;
  reg [3:0] rx_bit;
  reg [7:0] rx_shift;
  reg saw_z;

  initial begin
    fd = $fopen("results/uart_log.txt", "w");
    rx_busy = 0;
    saw_z = 0;
    cycles = 0;
    repeat (8) @(posedge clk);
    rst = 0;
  end

  always @(posedge clk) begin
    if (!rst) begin
      cycles = cycles + 1;
      if (cycles == 8000000) begin
        $display("TIMEOUT");
        $fclose(fd);
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
            $fwrite(fd, "%c", rx_shift);
            $write("%c", rx_shift);
            if (rx_shift == "Z")
              saw_z <= 1'b1;
            if (saw_z && rx_shift == 8'h0A) begin
              $fclose(fd);
              $display("\nDONE cycles=%0d", cycles);
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
