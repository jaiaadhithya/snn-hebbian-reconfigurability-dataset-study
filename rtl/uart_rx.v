`timescale 1ns / 1ps
// 8N1 UART receiver. valid is high for one clock when a byte is ready.

module uart_rx #(
  parameter integer CLK_HZ = 125000000,
  parameter integer BAUD   = 115200
)(
  input  wire       clk,
  input  wire       rst,
  input  wire       rx,
  output reg  [7:0] data,
  output reg        valid
);

  localparam integer CLKS_PER_BIT = CLK_HZ / BAUD;
  localparam integer HALF = CLKS_PER_BIT / 2;

  reg [1:0] rx_sync;
  reg [1:0] state;
  reg [15:0] clk_cnt;
  reg [3:0] bit_i;
  reg [7:0] shift;

  always @(posedge clk) begin
    if (rst) begin
      rx_sync <= 2'b11;
      state <= 2'd0;
      clk_cnt <= 16'd0;
      bit_i <= 4'd0;
      shift <= 8'd0;
      data <= 8'd0;
      valid <= 1'b0;
    end else begin
      rx_sync <= {rx_sync[0], rx};
      valid <= 1'b0;
      case (state)
        2'd0: begin
          if (rx_sync[1] == 1'b0) begin
            state <= 2'd1;
            clk_cnt <= 16'd0;
          end
        end
        2'd1: begin
          if (clk_cnt == HALF[15:0]) begin
            if (rx_sync[1] == 1'b0) begin
              state <= 2'd2;
              clk_cnt <= 16'd0;
              bit_i <= 4'd0;
            end else
              state <= 2'd0;
          end else
            clk_cnt <= clk_cnt + 16'd1;
        end
        2'd2: begin
          if (clk_cnt == CLKS_PER_BIT[15:0] - 16'd1) begin
            clk_cnt <= 16'd0;
            shift <= {rx_sync[1], shift[7:1]};
            if (bit_i == 4'd7)
              state <= 2'd3;
            else
              bit_i <= bit_i + 4'd1;
          end else
            clk_cnt <= clk_cnt + 16'd1;
        end
        default: begin
          if (clk_cnt == CLKS_PER_BIT[15:0] - 16'd1) begin
            state <= 2'd0;
            data <= shift;
            valid <= 1'b1;
            clk_cnt <= 16'd0;
          end else
            clk_cnt <= clk_cnt + 16'd1;
        end
      endcase
    end
  end

endmodule
