`timescale 1ns / 1ps
// 8N1 UART transmitter. Idle line is high.

module uart_tx #(
  parameter integer CLK_HZ = 125000000,
  parameter integer BAUD   = 115200
)(
  input  wire       clk,
  input  wire       rst,
  input  wire [7:0] data,
  input  wire       valid,
  output reg        tx,
  output reg        ready
);

  localparam integer CLKS_PER_BIT = CLK_HZ / BAUD;

  reg [15:0] clk_cnt;
  reg [3:0]  bit_cnt;
  reg [9:0]  shift_reg;

  always @(posedge clk) begin
    if (rst) begin
      tx        <= 1'b1;
      ready     <= 1'b1;
      clk_cnt   <= 16'd0;
      bit_cnt   <= 4'd0;
      shift_reg <= 10'h3FF;
    end else if (ready) begin
      tx <= 1'b1;
      if (valid) begin
        ready     <= 1'b0;
        shift_reg <= {1'b1, data, 1'b0};
        clk_cnt   <= 16'd0;
        bit_cnt   <= 4'd0;
        tx        <= 1'b0;
      end
    end else if (clk_cnt >= CLKS_PER_BIT[15:0] - 16'd1) begin
      clk_cnt <= 16'd0;
      if (bit_cnt < 4'd9) begin
        shift_reg <= {1'b1, shift_reg[9:1]};
        tx        <= shift_reg[1];
        bit_cnt   <= bit_cnt + 4'd1;
      end else begin
        ready <= 1'b1;
        tx    <= 1'b1;
      end
    end else
      clk_cnt <= clk_cnt + 16'd1;
  end

endmodule
