# Reconfigurable versus fixed mux SNN

This study runs on the ZedBoard. Both networks are the same circuit. The frozen pass never writes a mux or a weight. The reconfigurable pass uses the same samples with supervised Hebbian updates and may claim unused neurons. The laptop only records the UART stream and draws the figures.

Full MNIST does not fit in this bitstream. A 28×28 image is 784 pixels, and the training set is 60,000 of them. The large-input cases below are 8×8 binary patterns: 64 inputs and 10 classes, which is the same kind of comparison (many inputs, many classes) at a size the FPGA can store and run by itself.

## Network

- Up to 3 hidden layers, 8 neurons each, selected at runtime with `n_layers`.
- Each hidden neuron has one mux. Layer 0 selects one of the inputs. A deeper layer selects one neuron from the layer before it.
- Four output cells for the small cases, ten for the 64-input cases. The decision is the output with the larger accumulated synaptic drive over the 8 steps of the sample.
- Reset wiring: hidden neuron `h` listens to input `h` modulo the input count, and its strong output weight (96) goes to class `h` modulo the class count. The other output weights start at 8.
- Frozen pass: muxes and weights stay at that reset.
- Reconfigurable pass: on a training sample, the labeled column is potentiated by 16 when that hidden neuron spiked and depressed by 8 when it did not. One silent neuron that nobody has claimed yet can be pointed at the earliest spike in the previous layer. A second neuron is not claimed for that spike if one already listening there fired on this sample.

A neuron that never spikes is counted as unused. That count is the main structural metric.

## Cases

The cases are built so the two passes can come apart. A case where both are perfect, or both are at chance, does not say which wiring is useful.

| Case | Inputs | Classes | Layers | What differs |
| --- | --- | --- | --- | --- |
| aligned_small | 4 | 4 | 1 | One-hot patterns whose label matches the reset mux. The fixed wiring is already right. |
| permuted_small | 4 | 4 | 1 | The same patterns with the label rotated by one. The reset wiring points at the wrong class. |
| switched_small | 4 | 4 | 1 | Train the aligned labels, then train the rotated labels. Test the new regime and the old one. |
| noisy_small | 4 | 4 | 1 | Aligned labels, plus one extra input that is not the class. |
| random_small | 4 | 4 | 1 | Random 4-bit patterns and random labels. No class structure. |
| distinct64 | 64 | 10 | 1 | Ten classes, disjoint pixels, all of them outside inputs 0..7. A reset mux cannot see the class. |
| distinct64_3layer | 64 | 10 | 3 | The same disjoint classes. The recruited spike has to cross three mux layers. |
| glyphs64 | 64 | 10 | 1 | 8×8 digit drawings. Strokes overlap, so the earliest ink pixel is shared. |
| glyphs64_noise | 64 | 10 | 1 | Those drawings plus three random pixels in addresses 0..15, which is where a reset mux looks. The noise bits change from sample to sample. |
| permuted_3layer | 4 | 4 | 3 | Rotated labels again, through three hidden layers. |

`switched_small` is read by regime, not by the pooled score. The new-regime tests are group 2 and the old-regime tests are group 3. A network that tracks the change should get group 2 right and group 3 wrong. A frozen network should do the opposite. The pooled score can hide that.

## What gets recorded

After every test sample the FPGA sends:

- `R` — case, frozen or reconfigurable, sample index, regime group, true label, predicted label
- `T` — one line per timestep, with the input, hidden, and output spike bits

After each pass it sends `M`:

- test samples, correct count
- neurons used in layer 0, layer 1, and layer 2 (a neuron counts if it spiked at least once in the pass)
- spike totals for the inputs, each hidden layer, and the outputs

`Z` means the board has finished every case. The collector refuses to treat a log as complete until that line arrives.

## How to read a result

Reconfiguration is useful when the reset mux is pointed at the wrong place: the label was permuted, or the class pixels sit outside the inputs the reset mux can see. It is not useful when the reset wiring already matches the labels. It is also not useful when the label is noise, because a claimed neuron then follows a bit that will not be there on the next sample.

The 3-layer cases ask whether that claim still helps once the spike has to be recruited again at each layer. Eight neurons cannot hold ten private features, so the distinct 10-class cases can be right on at most eight classes even if recruitment works.
