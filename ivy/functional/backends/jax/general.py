"""Collection of Jax general functions, wrapped to fit Ivy syntax and signature."""

# global
import jax
import numpy as np
import jax.numpy as jnp
from numbers import Number
from operator import mul
from functools import reduce
from typing import Iterable, Optional, Union, Sequence, Callable
import multiprocessing as _multiprocessing


# necessary import, because stateful imports jax as soon as you import ivy, however,
# during multiversion # jax is not there, and therefore a later import results in some
# sort of circular import, so haiku is needed
import haiku  # NOQA


from haiku._src.data_structures import FlatMapping

# local
import ivy
from ivy.func_wrapper import with_unsupported_dtypes
from ivy.functional.backends.jax.device import _to_device, _to_array
from ivy.functional.backends.jax import JaxArray, NativeArray
from . import backend_version


def container_types():
    return [FlatMapping]


def current_backend_str() -> str:
    return "jax"


def is_native_array(x, /, *, exclusive=False):
    if exclusive:
        return isinstance(x, NativeArray)
    return isinstance(
        x,
        (
            NativeArray,
            jax.interpreters.ad.JVPTracer,
            jax.core.ShapedArray,
            jax.interpreters.partial_eval.DynamicJaxprTracer,
        ),
    )


def get_item(x: JaxArray, /, query: JaxArray, *, copy: bool = None) -> JaxArray:
    return x.__getitem__(query)


def array_equal(x0: JaxArray, x1: JaxArray, /) -> bool:
    return bool(jnp.array_equal(x0, x1))


def to_numpy(x: JaxArray, /, *, copy: bool = True) -> np.ndarray:
    if copy:
        return np.array(_to_array(x))
    else:
        return np.asarray(_to_array(x))


def to_scalar(x: JaxArray, /) -> Number:
    if isinstance(x, Number):
        return x
    else:
        return _to_array(x).item()


def to_list(x: JaxArray, /) -> list:
    return _to_array(x).tolist()


def gather(
    params: JaxArray,
    indices: JaxArray,
    /,
    *,
    axis: int = -1,
    batch_dims: int = 0,
    out: Optional[JaxArray] = None,
) -> JaxArray:
    axis = axis % len(params.shape)
    batch_dims = batch_dims % len(params.shape)
    ivy.utils.assertions.check_gather_input_valid(params, indices, axis, batch_dims)
    result = []
    if batch_dims == 0:
        result = jnp.take(params, indices, axis)
    else:
        for b in range(batch_dims):
            if b == 0:
                zip_list = [(p, i) for p, i in zip(params, indices)]
            else:
                zip_list = [
                    (p, i) for z in [zip(p1, i1) for p1, i1 in zip_list] for p, i in z
                ]
        for z in zip_list:
            p, i = z
            r = jnp.take(p, i, axis - batch_dims)
            result.append(r)
        result = jnp.array(result)
        result = result.reshape([*params.shape[0:batch_dims], *result.shape[1:]])
    return _to_device(result)


def gather_nd_helper(params, indices):
    indices_shape = indices.shape
    params_shape = params.shape
    if len(indices.shape) == 0:
        num_index_dims = 1
    else:
        num_index_dims = indices_shape[-1]
    res_dim_sizes_list = [
        reduce(mul, params_shape[i + 1 :], 1) for i in range(len(params_shape) - 1)
    ] + [1]
    result_dim_sizes = jnp.array(res_dim_sizes_list)
    implicit_indices_factor = int(result_dim_sizes[num_index_dims - 1].item())
    flat_params = jnp.reshape(params, (-1,))
    new_shape = [1] * (len(indices_shape) - 1) + [num_index_dims]
    indices_scales = jnp.reshape(result_dim_sizes[0:num_index_dims], new_shape)
    indices_for_flat_tiled = jnp.tile(
        jnp.reshape(jnp.sum(indices * indices_scales, -1, keepdims=True), (-1, 1)),
        (1, implicit_indices_factor),
    )
    implicit_indices = jnp.tile(
        jnp.expand_dims(jnp.arange(implicit_indices_factor), 0),
        (indices_for_flat_tiled.shape[0], 1),
    )
    indices_for_flat = indices_for_flat_tiled + implicit_indices
    flat_indices_for_flat = jnp.reshape(indices_for_flat, (-1,)).astype(jnp.int32)
    flat_gather = jnp.take(flat_params, flat_indices_for_flat, 0)
    new_shape = list(indices_shape[:-1]) + list(params_shape[num_index_dims:])
    ret = jnp.reshape(flat_gather, new_shape)
    return ret


def gather_nd(
    params: JaxArray,
    indices: JaxArray,
    /,
    *,
    batch_dims: int = 0,
    out: Optional[JaxArray] = None,
) -> JaxArray:
    ivy.utils.assertions.check_gather_nd_input_valid(params, indices, batch_dims)
    batch_dims = batch_dims % len(params.shape)
    result = []
    if batch_dims == 0:
        result = gather_nd_helper(params, indices)
    else:
        for b in range(batch_dims):
            if b == 0:
                zip_list = [(p, i) for p, i in zip(params, indices)]
            else:
                zip_list = [
                    (p, i) for z in [zip(p1, i1) for p1, i1 in zip_list] for p, i in z
                ]
        for z in zip_list:
            p, i = z
            r = gather_nd_helper(p, i)
            result.append(r)
        result = jnp.array(result)
        result = result.reshape([*params.shape[0:batch_dims], *result.shape[1:]])
    return _to_device(result)


def get_num_dims(x: JaxArray, /, *, as_array: bool = False) -> Union[JaxArray, int]:
    return jnp.asarray(len(jnp.shape(x))) if as_array else len(x.shape)


def inplace_arrays_supported():
    return False


def inplace_decrement(
    x: Union[ivy.Array, JaxArray], val: Union[ivy.Array, JaxArray]
) -> ivy.Array:
    (x_native, val_native), _ = ivy.args_to_native(x, val)
    if ivy.is_ivy_array(x):
        x.data -= val_native
    else:
        x = ivy.Array(x_native - val_native)
    return x


def inplace_increment(
    x: Union[ivy.Array, JaxArray], val: Union[ivy.Array, JaxArray]
) -> ivy.Array:
    (x_native, val_native), _ = ivy.args_to_native(x, val)
    if ivy.is_ivy_array(x):
        x.data += val_native
    else:
        x = ivy.Array(x_native + val_native)
    return x


def inplace_update(
    x: Union[ivy.Array, JaxArray],
    val: Union[ivy.Array, JaxArray],
    /,
    *,
    ensure_in_backend: bool = False,
    keep_input_dtype: bool = False,
) -> ivy.Array:
    if ivy.is_array(x) and ivy.is_array(val):
        if ensure_in_backend:
            raise ivy.utils.exceptions.IvyException(
                "JAX does not natively support inplace updates"
            )
        if keep_input_dtype:
            val = ivy.astype(val, x.dtype)
        (x_native, val_native), _ = ivy.args_to_native(x, val)
        if ivy.is_ivy_array(x):
            x.data = val_native
            # Handle view updates
            if ivy.exists(x._base):
                base = x._base
                base_idx = ivy.arange(base.size).reshape(base.shape)
                for fn, args, kwargs, index in x._manipulation_stack:
                    kwargs["copy"] = True
                    base_idx = ivy.__dict__[fn](base_idx, *args, **kwargs)
                    base_idx = base_idx[index] if ivy.exists(index) else base_idx
                base_flat = base.data.flatten()
                base_flat = base_flat.at[base_idx.data.flatten()].set(
                    val_native.flatten()
                )

                base.data = base_flat.reshape(base.shape)

                for ref in base._view_refs:
                    view = ref()
                    if ivy.exists(view) and view is not x:
                        _update_view(view, base)

            else:
                for ref in x._view_refs:
                    view = ref()
                    if ivy.exists(view):
                        _update_view(view, x)
        else:
            raise ivy.utils.exceptions.IvyException(
                "JAX does not natively support inplace updates"
            )
        return x
    else:
        return val


def _update_view(view, base):
    for fn, args, kwargs, index in view._manipulation_stack:
        base = ivy.__dict__[fn](base, *args, **kwargs)
        base = base[index] if ivy.exists(index) else base
    view.data = base.data
    return view


def inplace_variables_supported():
    return False


def multiprocessing(context: Optional[str] = None):
    return (
        _multiprocessing if context is None else _multiprocessing.get_context(context)
    )


def scatter_flat(
    indices: JaxArray,
    updates: JaxArray,
    /,
    *,
    size: Optional[int] = None,
    reduction: str = "sum",
    out: Optional[JaxArray] = None,
) -> JaxArray:
    target = out
    target_given = ivy.exists(target)
    if ivy.exists(size) and ivy.exists(target):
        ivy.utils.assertions.check_equal(len(target.shape), 1)
        ivy.utils.assertions.check_equal(target.shape[0], size)
    if reduction == "sum":
        if not target_given:
            target = jnp.zeros([size], dtype=updates.dtype)
        target = target.at[indices].add(updates)
    elif reduction == "replace":
        if not target_given:
            target = jnp.zeros([size], dtype=updates.dtype)
        target = target.at[indices].set(updates)
    elif reduction == "min":
        if not target_given:
            target = jnp.ones([size], dtype=updates.dtype) * 1e12
        target = target.at[indices].min(updates)
        if not target_given:
            target = jnp.where(target == 1e12, 0.0, target)
    elif reduction == "max":
        if not target_given:
            target = jnp.ones([size], dtype=updates.dtype) * -1e12
        target = target.at[indices].max(updates)
        if not target_given:
            target = jnp.where(target == -1e12, 0.0, target)
    else:
        raise ivy.utils.exceptions.IvyException(
            'reduction is {}, but it must be one of "sum", "min" or "max"'.format(
                reduction
            )
        )
    return _to_device(target)


scatter_flat.support_native_out = True


def scatter_nd(
    indices: JaxArray,
    updates: JaxArray,
    /,
    shape: Optional[Union[ivy.NativeShape, Sequence[int]]] = None,
    *,
    reduction: str = "sum",
    out: Optional[JaxArray] = None,
) -> JaxArray:
    # parse numeric inputs
    if (
        indices not in [Ellipsis, ()]
        and not (isinstance(indices, Iterable) and Ellipsis in indices)
        and not isinstance(indices, slice)
        and not (
            isinstance(indices, Iterable) and any(isinstance(k, slice) for k in indices)
        )
    ):
        indices = [[indices]] if isinstance(indices, Number) else indices
        indices = jnp.array(indices)
        if len(indices.shape) < 2:
            indices = jnp.expand_dims(indices, 0)
    # keep below commented out, array API tests are passing without this
    # updates = [updates] if isinstance(updates, Number) else updates

    updates = jnp.array(
        updates,
        dtype=ivy.dtype(out, as_native=True)
        if ivy.exists(out)
        else ivy.default_dtype(item=updates),
    )

    # handle Ellipsis
    if isinstance(indices, tuple) or indices is Ellipsis or isinstance(indices, slice):
        indices_tuple = indices
    else:
        expected_shape = (
            indices.shape[:-1] + out.shape[indices.shape[-1] :]
            if ivy.exists(out)
            else indices.shape[:-1] + tuple(shape[indices.shape[-1] :])
        )
        if sum(updates.shape) < sum(expected_shape):
            updates = ivy.broadcast_to(updates, expected_shape)._data
        elif sum(updates.shape) > sum(expected_shape):
            indices = ivy.broadcast_to(
                indices, updates.shape[:1] + (indices.shape[-1],)
            )._data
        indices_flat = indices.reshape(-1, indices.shape[-1]).T
        indices_tuple = tuple(indices_flat) + (Ellipsis,)

    # implementation
    target = out
    target_given = ivy.exists(target)
    if ivy.exists(shape) and ivy.exists(target):
        ivy.utils.assertions.check_equal(ivy.Shape(target.shape), ivy.Shape(shape))
    shape = list(shape) if ivy.exists(shape) else list(out.shape)
    if reduction == "sum":
        if not target_given:
            target = jnp.zeros(shape, dtype=updates.dtype)
        target = target.at[indices_tuple].add(updates)
    elif reduction == "replace":
        if not target_given:
            target = jnp.zeros(shape, dtype=updates.dtype)
        target = target.at[indices_tuple].set(updates)
    elif reduction == "min":
        if not target_given:
            target = jnp.ones(shape, dtype=updates.dtype) * 1e12
        target = target.at[indices_tuple].min(updates)
        if not target_given:
            target = jnp.asarray(
                jnp.where(target == 1e12, 0.0, target), dtype=updates.dtype
            )
    elif reduction == "max":
        if not target_given:
            target = jnp.ones(shape, dtype=updates.dtype) * -1e12
        target = target.at[indices_tuple].max(updates)
        if not target_given:
            target = jnp.asarray(
                jnp.where(target == -1e12, 0.0, target), dtype=updates.dtype
            )
    else:
        raise ivy.utils.exceptions.IvyException(
            'reduction is {}, but it must be one of "sum", "min" or "max"'.format(
                reduction
            )
        )
    if ivy.exists(out):
        return ivy.inplace_update(out, _to_device(target))
    return _to_device(target)


scatter_nd.support_native_out = True


def shape(
    x: JaxArray,
    /,
    *,
    as_array: bool = False,
) -> Union[ivy.Shape, ivy.Array]:
    if as_array:
        return ivy.array(jnp.shape(x), dtype=ivy.default_int_dtype())
    else:
        return ivy.Shape(x.shape)


def vmap(
    func: Callable,
    in_axes: Union[int, Sequence[int], Sequence[None]] = 0,
    out_axes: int = 0,
) -> Callable:
    func = ivy.output_to_native_arrays(func)
    return ivy.inputs_to_native_arrays(
        jax.vmap(func, in_axes=in_axes, out_axes=out_axes)
    )


@with_unsupported_dtypes({"0.3.14 and below": ("float16", "bfloat16")}, backend_version)
def isin(
    elements: JaxArray,
    test_elements: JaxArray,
    /,
    *,
    assume_unique: bool = False,
    invert: bool = False,
) -> JaxArray:
    return jnp.isin(elements, test_elements, assume_unique=assume_unique, invert=invert)


def itemsize(x: JaxArray) -> int:
    return x.itemsize
