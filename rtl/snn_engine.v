`timescale 1ns / 1ps
// Layered mux SNN.
// Each hidden neuron selects one spike from the previous layer.
// Outputs sum the last hidden layer through 8-bit weights.
// When learn_en is set, a training sample applies supervised Hebbian
// updates on the labeled output column and may claim one unused silent
// neuron per layer, pointing it at a spike that actually occurred.

module snn_engine #(
  parameter integer H       = 8,
  parameter integer MAX_L   = 3,
  parameter integer MAX_IN  = 64,
  parameter integer N_OUT   = 10,
  parameter integer STEPS   = 8
)(
  input  wire              clk,
  input  wire              rst,
  input  wire              pass_rst,
  input  wire              go,
  input  wire              learn_en,
  input  wire              train,
  input  wire [3:0]        n_layers,
  input  wire [6:0]        n_in,
  input  wire [3:0]        n_class,
  input  wire [3:0]        label,
  input  wire [MAX_IN-1:0] bits,
  output reg               busy,
  output reg               done,
  output reg  [3:0]        pred,
  output reg  [7:0]        util0,
  output reg  [7:0]        util1,
  output reg  [7:0]        util2,
  output reg  [15:0]       spikes_in,
  output reg  [15:0]       spikes_h0,
  output reg  [15:0]       spikes_h1,
  output reg  [15:0]       spikes_h2,
  output reg  [15:0]       spikes_out,
  output reg  [MAX_IN-1:0] saw_in,
  output reg  [MAX_L*H-1:0] saw_h,
  output reg  [N_OUT-1:0] saw_out,
  output reg  [STEPS*MAX_IN-1:0] rast_in,
  output reg  [STEPS*MAX_L*H-1:0] rast_h,
  output reg  [STEPS*N_OUT-1:0] rast_out
);

  localparam [7:0] LEAK     = 8'd5;
  localparam [7:0] THR_IN   = 8'd90;
  localparam [7:0] THR_H    = 8'd100;
  localparam [7:0] THR_OUT  = 8'd140;
  localparam [7:0] HEBB_POT = 8'd16;
  localparam [7:0] HEBB_DEP = 8'd8;

  localparam [1:0] PH_IDLE  = 2'd0;
  localparam [1:0] PH_STEP  = 2'd1;
  localparam [1:0] PH_LEARN = 2'd2;

  reg [1:0] phase;
  reg [3:0] step_i;
  reg [7:0] v_i [0:MAX_IN-1];
  reg [1:0] rf_i [0:MAX_IN-1];
  reg       sp_i [0:MAX_IN-1];
  reg [7:0] v_h [0:MAX_L*H-1];
  reg [1:0] rf_h [0:MAX_L*H-1];
  reg       sp_h [0:MAX_L*H-1];
  reg [7:0] v_o [0:N_OUT-1];
  reg [1:0] rf_o [0:N_OUT-1];
  reg       sp_o [0:N_OUT-1];
  reg [5:0] mux_sel [0:MAX_L*H-1];
  reg [7:0] w_h [0:MAX_L*H-1];
  reg [7:0] w_o [0:MAX_L*H*N_OUT-1];
  reg       frame_in [0:MAX_IN-1];
  reg       frame_h [0:MAX_L*H-1];
  reg       frame_out [0:N_OUT-1];
  reg       pass_h [0:MAX_L*H-1];
  reg       owned [0:MAX_L*H-1];
  reg [15:0] drive [0:N_OUT-1];

  integer i, l, h, o, pre, idx;
  integer n_add_in, n_add_h0, n_add_h1, n_add_h2, n_add_out;
  reg [10:0] nv;
  reg [11:0] cur;
  reg [15:0] acc;
  reg [5:0] src;
  reg [5:0] first_pre;
  reg       found_pre;
  reg       taken;
  reg       already;
  reg       pre_on;

  function [7:0] hebb;
    input [7:0] w;
    input       pre_f;
    input       post_f;
    reg [8:0] sum;
    begin
      if (pre_f && post_f) begin
        sum = {1'b0, w} + HEBB_POT;
        hebb = sum[8] ? 8'hFF : sum[7:0];
      end else if (pre_f || post_f)
        hebb = (w > HEBB_DEP) ? (w - HEBB_DEP) : 8'd0;
      else
        hebb = w;
    end
  endfunction

  function [10:0] lif8;
    input [7:0] v;
    input [1:0] rf;
    input [11:0] icur;
    input [7:0] thr;
    reg [12:0] sum;
    begin
      if (rf != 2'd0)
        lif8 = {1'b0, rf - 2'd1, 8'd0};
      else begin
        if (v > LEAK)
          sum = (v - LEAK) + icur;
        else
          sum = icur;
        if (sum >= {5'd0, thr})
          lif8 = {1'b1, 2'd2, 8'd0};
        else if (sum > 13'd255)
          lif8 = {1'b0, 2'd0, 8'd255};
        else
          lif8 = {1'b0, 2'd0, sum[7:0]};
      end
    end
  endfunction

  always @(posedge clk) begin
    if (rst) begin
      phase <= PH_IDLE;
      busy <= 1'b0;
      done <= 1'b0;
      pred <= 4'd0;
      util0 <= 8'd0;
      util1 <= 8'd0;
      util2 <= 8'd0;
      spikes_in <= 16'd0;
      spikes_h0 <= 16'd0;
      spikes_h1 <= 16'd0;
      spikes_h2 <= 16'd0;
      spikes_out <= 16'd0;
      saw_in <= {MAX_IN{1'b0}};
      saw_h <= {MAX_L*H{1'b0}};
      saw_out <= {N_OUT{1'b0}};
      rast_in <= {STEPS*MAX_IN{1'b0}};
      rast_h <= {STEPS*MAX_L*H{1'b0}};
      rast_out <= {STEPS*N_OUT{1'b0}};
    end else if (pass_rst) begin
      phase <= PH_IDLE;
      busy <= 1'b0;
      done <= 1'b0;
      pred <= 4'd0;
      util0 <= 8'd0;
      util1 <= 8'd0;
      util2 <= 8'd0;
      spikes_in <= 16'd0;
      spikes_h0 <= 16'd0;
      spikes_h1 <= 16'd0;
      spikes_h2 <= 16'd0;
      spikes_out <= 16'd0;
      for (i = 0; i < MAX_L * H; i = i + 1) begin
        pass_h[i] <= 1'b0;
        owned[i] <= 1'b0;
        w_h[i] <= 8'd180;
        if (i < H)
          mux_sel[i] <= i % n_in;
        else
          mux_sel[i] <= (i % H);
        for (o = 0; o < N_OUT; o = o + 1)
          w_o[i * N_OUT + o] <= (((i % H) % n_class) == o) ? 8'd96 : 8'd8;
      end
    end else begin
      done <= 1'b0;
      case (phase)
        PH_IDLE: begin
          busy <= 1'b0;
          if (go) begin
            busy <= 1'b1;
            step_i <= 4'd0;
            phase <= PH_STEP;
            for (i = 0; i < MAX_IN; i = i + 1) begin
              v_i[i] <= 8'd0;
              rf_i[i] <= 2'd0;
              sp_i[i] <= 1'b0;
              frame_in[i] <= 1'b0;
            end
            for (i = 0; i < MAX_L * H; i = i + 1) begin
              v_h[i] <= 8'd0;
              rf_h[i] <= 2'd0;
              sp_h[i] <= 1'b0;
              frame_h[i] <= 1'b0;
            end
            for (o = 0; o < N_OUT; o = o + 1) begin
              v_o[o] <= 8'd0;
              rf_o[o] <= 2'd0;
              sp_o[o] <= 1'b0;
              frame_out[o] <= 1'b0;
              drive[o] <= 16'd0;
            end
          end
        end
        PH_STEP: begin
          n_add_in = 0;
          n_add_h0 = 0;
          n_add_h1 = 0;
          n_add_h2 = 0;
          n_add_out = 0;
          for (i = 0; i < MAX_IN; i = i + 1) begin
            cur = (i < n_in && bits[i]) ? 12'd240 : 12'd0;
            nv = lif8(v_i[i], rf_i[i], cur, THR_IN);
            sp_i[i] <= nv[10];
            rf_i[i] <= nv[9:8];
            v_i[i] <= nv[7:0];
            frame_in[i] <= frame_in[i] | nv[10];
            rast_in[step_i * MAX_IN + i] <= nv[10];
            if (nv[10])
              n_add_in = n_add_in + 1;
          end
          spikes_in <= spikes_in + n_add_in[15:0];
          for (l = 0; l < MAX_L; l = l + 1) begin
            for (h = 0; h < H; h = h + 1) begin
              idx = l * H + h;
              src = mux_sel[idx];
              if (l >= n_layers)
                cur = 12'd0;
              else if (l == 0)
                cur = sp_i[src] ? {4'd0, w_h[idx]} : 12'd0;
              else
                cur = sp_h[(l - 1) * H + src] ? {4'd0, w_h[idx]} : 12'd0;
              nv = lif8(v_h[idx], rf_h[idx], cur, THR_H);
              sp_h[idx] <= nv[10];
              rf_h[idx] <= nv[9:8];
              v_h[idx] <= nv[7:0];
              frame_h[idx] <= frame_h[idx] | nv[10];
              rast_h[step_i * MAX_L * H + idx] <= nv[10];
              if (nv[10] && l < n_layers) begin
                if (l == 0) n_add_h0 = n_add_h0 + 1;
                else if (l == 1) n_add_h1 = n_add_h1 + 1;
                else n_add_h2 = n_add_h2 + 1;
              end
            end
          end
          spikes_h0 <= spikes_h0 + n_add_h0[15:0];
          spikes_h1 <= spikes_h1 + n_add_h1[15:0];
          spikes_h2 <= spikes_h2 + n_add_h2[15:0];
          for (o = 0; o < N_OUT; o = o + 1) begin
            acc = 16'd0;
            if (o < n_class) begin
              for (h = 0; h < H; h = h + 1) begin
                idx = (n_layers - 1) * H + h;
                if (sp_h[idx])
                  acc = acc + w_o[idx * N_OUT + o];
              end
            end
            drive[o] <= drive[o] + acc;
            nv = lif8(v_o[o], rf_o[o], acc[11:0], THR_OUT);
            sp_o[o] <= nv[10];
            rf_o[o] <= nv[9:8];
            v_o[o] <= nv[7:0];
            frame_out[o] <= frame_out[o] | nv[10];
            rast_out[step_i * N_OUT + o] <= nv[10];
            if (nv[10] && o < n_class)
              n_add_out = n_add_out + 1;
          end
          spikes_out <= spikes_out + n_add_out[15:0];
          if (step_i == STEPS - 1)
            phase <= PH_LEARN;
          else
            step_i <= step_i + 4'd1;
        end
        PH_LEARN: begin
          if (learn_en && train) begin
            for (l = 0; l < MAX_L; l = l + 1) begin
              if (l < n_layers) begin
                found_pre = 1'b0;
                first_pre = 6'd0;
                taken = 1'b0;
                already = 1'b0;
                if (l == 0) begin
                  for (pre = 0; pre < MAX_IN; pre = pre + 1)
                    if (!found_pre && pre < n_in && frame_in[pre]) begin
                      found_pre = 1'b1;
                      first_pre = pre[5:0];
                    end
                end else begin
                  for (pre = 0; pre < H; pre = pre + 1)
                    if (!found_pre && frame_h[(l - 1) * H + pre]) begin
                      found_pre = 1'b1;
                      first_pre = pre[5:0];
                    end
                end
                for (h = 0; h < H; h = h + 1)
                  if ((owned[l * H + h] || frame_h[l * H + h]) &&
                      mux_sel[l * H + h] == first_pre && found_pre)
                    already = 1'b1;
                for (h = 0; h < H; h = h + 1) begin
                  idx = l * H + h;
                  src = mux_sel[idx];
                  if (l == 0)
                    pre_on = frame_in[src];
                  else
                    pre_on = frame_h[(l - 1) * H + src];
                  w_h[idx] <= hebb(w_h[idx], pre_on, frame_h[idx]);
                  if (frame_h[idx])
                    owned[idx] <= 1'b1;
                  else if (!already && !taken && !owned[idx] && found_pre) begin
                    mux_sel[idx] <= first_pre;
                    owned[idx] <= 1'b1;
                    taken = 1'b1;
                  end
                  if (l + 1 == n_layers && label < n_class)
                    w_o[idx * N_OUT + label] <= hebb(
                      w_o[idx * N_OUT + label], frame_h[idx], 1'b1);
                end
              end
            end
          end
          begin : SCORE
            reg [15:0] best_drive;
            reg [3:0] best_i;
            reg [7:0] c0, c1, c2;
            best_drive = 16'd0;
            best_i = 4'd0;
            c0 = 8'd0;
            c1 = 8'd0;
            c2 = 8'd0;
            for (o = 0; o < N_OUT; o = o + 1)
              if (o < n_class && drive[o] > best_drive) begin
                best_drive = drive[o];
                best_i = o[3:0];
              end
            pred <= best_i;
            for (h = 0; h < H; h = h + 1) begin
              pass_h[h] <= pass_h[h] | frame_h[h];
              pass_h[H + h] <= pass_h[H + h] | frame_h[H + h];
              pass_h[2 * H + h] <= pass_h[2 * H + h] | frame_h[2 * H + h];
              if (pass_h[h] | frame_h[h]) c0 = c0 + 8'd1;
              if ((pass_h[H + h] | frame_h[H + h]) && n_layers > 4'd1) c1 = c1 + 8'd1;
              if ((pass_h[2 * H + h] | frame_h[2 * H + h]) && n_layers > 4'd2) c2 = c2 + 8'd1;
            end
            util0 <= c0;
            util1 <= c1;
            util2 <= c2;
          end
          for (i = 0; i < MAX_IN; i = i + 1)
            saw_in[i] <= frame_in[i];
          for (i = 0; i < MAX_L * H; i = i + 1)
            saw_h[i] <= frame_h[i];
          for (o = 0; o < N_OUT; o = o + 1)
            saw_out[o] <= frame_out[o];
          done <= 1'b1;
          busy <= 1'b0;
          phase <= PH_IDLE;
        end
        default: phase <= PH_IDLE;
      endcase
    end
  end

endmodule
