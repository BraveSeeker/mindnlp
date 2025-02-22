# Copyright 2022 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""MindNLP MindSpore Utils"""
# pylint: disable=C0412
# pylint: disable=C0103

import inspect

import mindspore
from mindspore import nn, ops, Parameter
from mindspore.common.initializer import initializer, Normal

from mindnlp._legacy.nn import Matmul

ALL_LAYERNORM_LAYERS = [nn.LayerNorm]

class Conv1D(nn.Cell):
    """
    1D-convolutional layer Basically works like a linear layer but the weights are transposed.

    Args:
        n_out (`int`): The number of output features.
        n_in (`int`): The number of input features.
    """

    def __init__(self, n_out, n_in):
        super().__init__()
        self.n_out = n_out
        self.weight = Parameter(initializer(Normal(sigma=0.02), (n_in, n_out), mindspore.float32))
        self.bias = Parameter(ops.zeros(n_out))
        self.matmul = Matmul()

    def construct(self, x):
        size_out = x.shape[:-1] + (self.n_out,)
        x = self.matmul(x.view(-1, x.shape[-1]), self.weight) + self.bias
        x = x.view(size_out)
        return x


def prune_conv1d_layer(layer, index, axis=1):
    """
    Prune a Conv1D layer to keep only entries in index. A Conv1D work as a Linear layer (see e.g. BERT) but the weights
    are transposed.

    Used to remove heads.

    Args:
        layer ([`~mindspore_utils.Conv1D`]): The layer to prune.
        index (`mindspore.Tensor[int64]`): The indices to keep in the layer.
        axis (`int`, *optional*, defaults to 1): The dimension on which to keep the indices.

    Returns:
        [`~mindspore_utils.Conv1D`]: The pruned layer as a new layer with `requires_grad=True`.
    """
    gama_l = layer.weight.index_select(axis, index)
    if axis == 0:
        beta_l = layer.bias
    else:
        beta_l = layer.bias[index]
    new_size = list(layer.weight.shape)
    new_size[axis] = len(index)
    new_layer = Conv1D(new_size[1], new_size[0])
    new_layer.weight.requires_grad = False
    new_layer.weight = gama_l.copy()
    new_layer.weight.requires_grad = True
    new_layer.bias.requires_grad = False
    new_layer.bias = beta_l.copy()
    new_layer.bias.requires_grad = True
    return new_layer


def find_pruneable_heads_and_indices(heads, n_heads, head_size, already_pruned_heads):
    """
    Finds the heads and their indices taking `already_pruned_heads` into account.

    Args:
        heads (`List[int]`): List of the indices of heads to prune.
        n_heads (`int`): The number of heads in the model.
        head_size (`int`): The size of each head.
        already_pruned_heads (`Set[int]`): A set of already pruned heads.

    Returns:
        `Tuple[Set[int], MindSpore.Tensor[int64]]`: A tuple with the remaining heads and their corresponding indices.
    """
    mask = ops.ones((n_heads, head_size))
    heads = set(heads) - already_pruned_heads  # Convert to set and remove already pruned heads
    for head in heads:
        # Compute how many pruned heads are before the head and move the index accordingly
        head = head - sum(1 if h < head else 0 for h in already_pruned_heads)
        mask[head] = 0
    mask = mask.view(-1).eq(1)
    index = ops.arange(len(mask), dtype=mindspore.int64)[mask]
    return heads, index

def prune_linear_layer(layer, index, axis=0):
    """
    Prune a linear layer to keep only entries in index.
    Used to remove heads.
    Args:
        layer (`mindspore.nn.Dense`): The layer to prune.
        index (`mindspore.Tensor[int64]`): The indices to keep in the layer.
        axis (`int`, *optional*, defaults to 0): The dimension on which to keep the indices.
    Returns:
        `mindspore.nn.Dense`: The pruned layer as a new layer with `requires_grad=True`.
    """
    W = layer.weight.index_select(axis, index).copy()
    if layer.bias is not None:
        if axis == 1:
            b = layer.bias.copy()
        else:
            b = layer.bias[index].copy()
    new_size = list(layer.weight.shape)
    new_size[axis] = len(index)
    new_layer = nn.Dense(new_size[1], new_size[0], has_bias=layer.bias is not None)
    new_layer.weight.requires_grad = False
    new_layer.weight.set_data(W)
    new_layer.weight.requires_grad = True
    if layer.bias is not None:
        new_layer.bias.requires_grad = False
        new_layer.bias.set_data(b)
        new_layer.bias.requires_grad = True
    return new_layer


def apply_chunking_to_forward(forward_fn, chunk_size, chunk_axis, *input_tensors):
    """
    This function chunks the `input_tensors` into smaller input tensor parts of size `chunk_size` over the dimension
    `chunk_axis`. It then applies a layer `forward_fn` to each chunk independently to save memory.
    If the `forward_fn` is independent across the `chunk_dim` this function will yield the same result as directly
    applying `forward_fn` to `input_tensors`.
    Args:
        forward_fn (`Callable[..., mindspore.Tensor]`):
            The forward function of the model.
        chunk_size (`int`):
            The chunk size of a chunked tensor: `num_chunks = len(input_tensors[0]) / chunk_size`.
        chunk_axis (`int`):
            The dimension over which the `input_tensors` should be chunked.
        input_tensors (`Tuple[mindspore.Tensor]`):
            The input tensors of `forward_fn` which will be chunked
    Returns:
        `mindspore.Tensor`: A tensor with the same shape as the `forward_fn` would have given if applied`.
    """
    assert len(input_tensors) > 0, f"{input_tensors} has to be a tuple/list of tensors"

     # inspect.signature exist since python 3.5 and is a python method -> no problem with backward compatibility
    num_args_in_forward_chunk_fn = len(inspect.signature(forward_fn).parameters)
    if num_args_in_forward_chunk_fn != len(input_tensors):
        raise ValueError(
            f"forward_chunk_fn expects {num_args_in_forward_chunk_fn} arguments, but only {len(input_tensors)} input "
            "tensors are given"
        )

    if chunk_size > 0:
        tensor_shape = input_tensors[0].shape[chunk_axis]
        for input_tensor in input_tensors:
            if input_tensor.shape[chunk_axis] != tensor_shape:
                raise ValueError(
                    f"All input tenors have to be of the same shape: {tensor_shape}, "
                    f"found shape {input_tensor.shape[chunk_axis]}"
                )

        if input_tensors[0].shape[chunk_axis] % chunk_size != 0:
            raise ValueError(
                f"The dimension to be chunked {input_tensors[0].shape[chunk_axis]} has to be a multiple of the chunk "
                f"size {chunk_size}"
            )

        num_chunks = input_tensors[0].shape[chunk_axis] // chunk_size

        # chunk input tensor into tuples
        input_tensors_chunks = tuple(input_tensor.chunk(num_chunks, axis=chunk_axis) for input_tensor in input_tensors)
        # apply forward fn to every tuple
        output_chunks = tuple(forward_fn(*input_tensors_chunk) for input_tensors_chunk in zip(*input_tensors_chunks))
        # concatenate output at same dimension
        return ops.cat(output_chunks, axis=chunk_axis)

    return forward_fn(*input_tensors)

def zero_init(cls, *args, **kwargs):
    """init zeros to speed up initialize stage."""
    for k in kwargs.keys():# pylint: disable=consider-iterating-dictionary
        if 'init' in k:
            kwargs.pop(k)
    init_signature = inspect.signature(cls.__init__)
    init_params = init_signature.parameters
    for param_name in init_params.keys():
        if 'init' in param_name:
            kwargs[param_name] = 'zeros'
    def _reset_parameters(self): pass # pylint: disable=multiple-statements, unused-argument
    cls.reset_parameters = _reset_parameters
    return cls(*args, **kwargs)
