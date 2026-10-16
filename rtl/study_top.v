`timescale 1ns / 1ps
// Runs every stored case twice: frozen mux/weights, then the same samples
// with Hebbian updates and neuron recruitment. Scores and per-step rasters
// leave on uart_tx as ASCII lines. The host only records; it does not
// supply the samples.

module study_top #(
  parameter integer CLK_HZ = 125000000,
  parameter integer BAUD   = 115200
)(
  input  wire       clk,
  input  wire       rst,
  output wire       uart_tx,
  output wire [7:0] leds
);

  localparam [3:0] ST_RST_PASS = 4'd0;
  localparam [3:0] ST_GO       = 4'd1;
  localparam [3:0] ST_WAIT     = 4'd2;
  localparam [3:0] ST_PREP_R   = 4'd3;
  localparam [3:0] ST_WAIT_R   = 4'd4;
  localparam [3:0] ST_PREP_T   = 4'd5;
  localparam [3:0] ST_WAIT_T   = 4'd6;
  localparam [3:0] ST_NEXT     = 4'd7;
  localparam [3:0] ST_PREP_M   = 4'd8;
  localparam [3:0] ST_WAIT_M   = 4'd9;
  localparam [3:0] ST_ADV      = 4'd10;
  localparam [3:0] ST_PREP_Z   = 4'd11;
  localparam [3:0] ST_WAIT_Z   = 4'd12;
  localparam [3:0] ST_HALT     = 4'd13;

  localparam [1:0] PS_IDLE = 2'd0;
  localparam [1:0] PS_HIGH = 2'd1;
  localparam [1:0] PS_LOW  = 2'd2;

  reg [3:0] state;
  reg [3:0] case_i;
  reg [7:0] sample_i;
  reg       mode;
  reg [7:0] correct;
  reg [3:0] step_send;
  reg       finished;

  reg        pass_rst;
  reg        go;
  reg        arm;
  reg [6:0]  msg_len;
  reg [7:0]  msg [0:79];
  integer    wr;

  reg [1:0]  pst;
  reg [6:0]  idx;
  reg        tx_valid;
  reg [7:0]  tx_data;
  reg        send_done;
  wire       tx_ready;

  wire [63:0] bits;
  wire [3:0]  label;
  wire        train;
  wire [1:0]  group;
  wire [3:0]  n_layers;
  wire [6:0]  n_in;
  wire [3:0]  n_class;
  wire [7:0]  n_train;
  wire [7:0]  n_test;
  wire [7:0]  n_cases;
  wire [8:0]  n_samp = {1'b0, n_train} + {1'b0, n_test};

  wire        busy;
  wire        done;
  wire [3:0]  pred;
  wire [7:0]  util0, util1, util2;
  wire [15:0] spikes_in, spikes_h0, spikes_h1, spikes_h2, spikes_out;
  wire [63:0] saw_in;
  wire [23:0] saw_h;
  wire [9:0]  saw_out;
  wire [8*64-1:0] rast_in;
  wire [8*24-1:0] rast_h;
  wire [8*10-1:0] rast_out;

  reg [63:0] slice_in;
  reg [23:0] slice_h;
  reg [11:0] slice_out;
  integer b;

  function [7:0] hexch;
    input [3:0] n;
    begin
      if (n < 4'd10)
        hexch = 8'h30 + n;
      else
        hexch = 8'h37 + n;
    end
  endfunction

  study_rom rom (
    .case_i(case_i),
    .sample_i(sample_i),
    .bits(bits),
    .label(label),
    .train(train),
    .group(group),
    .n_layers(n_layers),
    .n_in(n_in),
    .n_class(n_class),
    .n_train(n_train),
    .n_test(n_test),
    .n_cases(n_cases)
  );

  snn_engine eng (
    .clk(clk),
    .rst(rst),
    .pass_rst(pass_rst),
    .go(go),
    .learn_en(mode),
    .train(train),
    .n_layers(n_layers),
    .n_in(n_in),
    .n_class(n_class),
    .label(label),
    .bits(bits),
    .busy(busy),
    .done(done),
    .pred(pred),
    .util0(util0),
    .util1(util1),
    .util2(util2),
    .spikes_in(spikes_in),
    .spikes_h0(spikes_h0),
    .spikes_h1(spikes_h1),
    .spikes_h2(spikes_h2),
    .spikes_out(spikes_out),
    .saw_in(saw_in),
    .saw_h(saw_h),
    .saw_out(saw_out),
    .rast_in(rast_in),
    .rast_h(rast_h),
    .rast_out(rast_out)
  );

  uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) uart (
    .clk(clk),
    .rst(rst),
    .data(tx_data),
    .valid(tx_valid),
    .tx(uart_tx),
    .ready(tx_ready)
  );

  // Byte pump. A message is armed for one clock, then bytes wait for the
  // transmitter to accept each one before the next is offered.
  always @(posedge clk) begin
    if (rst) begin
      pst <= PS_IDLE;
      idx <= 7'd0;
      tx_valid <= 1'b0;
      tx_data <= 8'd0;
      send_done <= 1'b0;
    end else begin
      tx_valid <= 1'b0;
      send_done <= 1'b0;
      if (arm) begin
        pst <= PS_HIGH;
        idx <= 7'd0;
      end else begin
        case (pst)
          PS_HIGH: begin
            if (tx_ready) begin
              tx_valid <= 1'b1;
              tx_data <= msg[idx];
              pst <= PS_LOW;
            end
          end
          PS_LOW: begin
            if (!tx_ready) begin
              if (idx + 7'd1 >= msg_len) begin
                pst <= PS_IDLE;
                send_done <= 1'b1;
              end else begin
                idx <= idx + 7'd1;
                pst <= PS_HIGH;
              end
            end
          end
          default: pst <= PS_IDLE;
        endcase
      end
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      state <= ST_RST_PASS;
      case_i <= 4'd0;
      sample_i <= 8'd0;
      mode <= 1'b0;
      correct <= 8'd0;
      step_send <= 4'd0;
      finished <= 1'b0;
      pass_rst <= 1'b0;
      go <= 1'b0;
      arm <= 1'b0;
      msg_len <= 7'd0;
    end else begin
      pass_rst <= 1'b0;
      go <= 1'b0;
      arm <= 1'b0;
      case (state)
        ST_RST_PASS: begin
          pass_rst <= 1'b1;
          correct <= 8'd0;
          sample_i <= 8'd0;
          state <= ST_GO;
        end
        ST_GO: begin
          go <= 1'b1;
          state <= ST_WAIT;
        end
        ST_WAIT: begin
          if (done) begin
            if (!train && pred == label && correct != 8'hFF)
              correct <= correct + 8'd1;
            if (train)
              state <= ST_NEXT;
            else begin
              step_send <= 4'd0;
              state <= ST_PREP_R;
            end
          end
        end
        ST_PREP_R: begin
          wr = 0;
          msg[wr] = "R"; wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(case_i[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = mode ? "1" : "0"; wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(sample_i[7:4]); wr = wr + 1;
          msg[wr] = hexch(sample_i[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch({2'b0, group}); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(label); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(pred); wr = wr + 1;
          msg[wr] = 8'h0A; wr = wr + 1;
          msg_len <= wr[6:0];
          arm <= 1'b1;
          state <= ST_WAIT_R;
        end
        ST_WAIT_R: begin
          if (send_done)
            state <= ST_PREP_T;
        end
        ST_PREP_T: begin
          for (b = 0; b < 64; b = b + 1)
            slice_in[b] = rast_in[step_send * 64 + b];
          for (b = 0; b < 24; b = b + 1)
            slice_h[b] = rast_h[step_send * 24 + b];
          for (b = 0; b < 10; b = b + 1)
            slice_out[b] = rast_out[step_send * 10 + b];
          slice_out[11:10] = 2'b00;
          wr = 0;
          msg[wr] = "T"; wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(case_i[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = mode ? "1" : "0"; wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(sample_i[7:4]); wr = wr + 1;
          msg[wr] = hexch(sample_i[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(step_send); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          for (b = 15; b >= 0; b = b - 1) begin
            msg[wr] = hexch(slice_in[b * 4 +: 4]);
            wr = wr + 1;
          end
          msg[wr] = ","; wr = wr + 1;
          for (b = 5; b >= 0; b = b - 1) begin
            msg[wr] = hexch(slice_h[b * 4 +: 4]);
            wr = wr + 1;
          end
          msg[wr] = ","; wr = wr + 1;
          for (b = 2; b >= 0; b = b - 1) begin
            msg[wr] = hexch(slice_out[b * 4 +: 4]);
            wr = wr + 1;
          end
          msg[wr] = 8'h0A; wr = wr + 1;
          msg_len <= wr[6:0];
          arm <= 1'b1;
          state <= ST_WAIT_T;
        end
        ST_WAIT_T: begin
          if (send_done) begin
            if (step_send == 4'd7)
              state <= ST_NEXT;
            else begin
              step_send <= step_send + 4'd1;
              state <= ST_PREP_T;
            end
          end
        end
        ST_NEXT: begin
          if ({1'b0, sample_i} + 9'd1 < n_samp) begin
            sample_i <= sample_i + 8'd1;
            state <= ST_GO;
          end else
            state <= ST_PREP_M;
        end
        ST_PREP_M: begin
          wr = 0;
          msg[wr] = "M"; wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(case_i[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = mode ? "1" : "0"; wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(n_layers); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch({1'b0, n_in[6:4]}); wr = wr + 1;
          msg[wr] = hexch(n_in[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(n_class); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(n_test[7:4]); wr = wr + 1;
          msg[wr] = hexch(n_test[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(correct[7:4]); wr = wr + 1;
          msg[wr] = hexch(correct[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(util0[7:4]); wr = wr + 1;
          msg[wr] = hexch(util0[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(util1[7:4]); wr = wr + 1;
          msg[wr] = hexch(util1[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(util2[7:4]); wr = wr + 1;
          msg[wr] = hexch(util2[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(spikes_in[15:12]); wr = wr + 1;
          msg[wr] = hexch(spikes_in[11:8]); wr = wr + 1;
          msg[wr] = hexch(spikes_in[7:4]); wr = wr + 1;
          msg[wr] = hexch(spikes_in[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(spikes_h0[15:12]); wr = wr + 1;
          msg[wr] = hexch(spikes_h0[11:8]); wr = wr + 1;
          msg[wr] = hexch(spikes_h0[7:4]); wr = wr + 1;
          msg[wr] = hexch(spikes_h0[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(spikes_h1[15:12]); wr = wr + 1;
          msg[wr] = hexch(spikes_h1[11:8]); wr = wr + 1;
          msg[wr] = hexch(spikes_h1[7:4]); wr = wr + 1;
          msg[wr] = hexch(spikes_h1[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(spikes_h2[15:12]); wr = wr + 1;
          msg[wr] = hexch(spikes_h2[11:8]); wr = wr + 1;
          msg[wr] = hexch(spikes_h2[7:4]); wr = wr + 1;
          msg[wr] = hexch(spikes_h2[3:0]); wr = wr + 1;
          msg[wr] = ","; wr = wr + 1;
          msg[wr] = hexch(spikes_out[15:12]); wr = wr + 1;
          msg[wr] = hexch(spikes_out[11:8]); wr = wr + 1;
          msg[wr] = hexch(spikes_out[7:4]); wr = wr + 1;
          msg[wr] = hexch(spikes_out[3:0]); wr = wr + 1;
          msg[wr] = 8'h0A; wr = wr + 1;
          msg_len <= wr[6:0];
          arm <= 1'b1;
          state <= ST_WAIT_M;
        end
        ST_WAIT_M: begin
          if (send_done)
            state <= ST_ADV;
        end
        ST_ADV: begin
          if (!mode) begin
            mode <= 1'b1;
            state <= ST_RST_PASS;
          end else if ({4'd0, case_i} + 8'd1 < n_cases) begin
            case_i <= case_i + 4'd1;
            mode <= 1'b0;
            state <= ST_RST_PASS;
          end else
            state <= ST_PREP_Z;
        end
        ST_PREP_Z: begin
          msg[0] = "Z";
          msg[1] = 8'h0A;
          msg_len <= 7'd2;
          arm <= 1'b1;
          state <= ST_WAIT_Z;
        end
        ST_WAIT_Z: begin
          if (send_done) begin
            finished <= 1'b1;
            state <= ST_HALT;
          end
        end
        ST_HALT: finished <= 1'b1;
        default: state <= ST_RST_PASS;
      endcase
    end
  end

  assign leds[3:0] = case_i;
  assign leds[4]   = mode;
  assign leds[5]   = busy;
  assign leds[6]   = (pst != PS_IDLE);
  assign leds[7]   = finished;

endmodule
