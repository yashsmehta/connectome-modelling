import numpy as np
import jax.numpy as jnp
import jax
from jax import vmap
from jax.lax import reshape
from jax.random import bernoulli, split
from functools import partial
from jax.nn import sigmoid
from jax.experimental.host_callback import id_print
import collections

from plasticity.utils import create_nested_list
from plasticity import inputs


def simulate_fly_trial(
    key,
    weights,
    plasticity_coeffs,
    plasticity_func,
    rewards_in_arena,
    r_history,
    odor_mus,
    odor_sigmas,
):
    input_xs, trial_odors, decisions = [], [], []

    expected_reward = np.mean(r_history)

    while True:
        key, subkey = split(key)
        odor = int(bernoulli(key, 0.5))
        trial_odors.append(odor)
        x = inputs.sample_inputs(odor_mus, odor_sigmas, odor, subkey)
        prob_output = sigmoid(jnp.dot(x, weights))
        key, subkey = split(key)
        sampled_output = float(bernoulli(subkey, prob_output))

        input_xs.append(x)
        decisions.append(sampled_output)

        if sampled_output == 1:
            reward = rewards_in_arena[odor]
            r_history.appendleft(reward)
            rewards_in_arena[odor] = 0
            dw = weight_update(
                x, weights, plasticity_coeffs, plasticity_func, reward, expected_reward
            )
            weights += dw
            break

    return (input_xs, trial_odors, decisions, reward, expected_reward), weights, rewards_in_arena, r_history


def simulate_fly_experiment(
    key,
    weights,
    plasticity_coeffs,
    plasticity_func,
    odor_mus,
    odor_sigmas,
    reward_ratios,
    trials_per_block,
    moving_avg_window=10,
):
    num_blocks = len(reward_ratios)
    r_history = collections.deque(moving_avg_window*[0], maxlen=moving_avg_window)
    rewards_in_arena = np.zeros(2,)

    xs, odors, sampled_ys, rewards, expected_rewards = (
        create_nested_list(num_blocks, trials_per_block) for _ in range(5)
    )

    for block in range(len(reward_ratios)):
        r_ratio = reward_ratios[block]
        for trial in range(trials_per_block):
            key, _ = split(key)
            sampled_rewards = bernoulli(key, np.array(r_ratio))
            rewards_in_arena = np.logical_or(sampled_rewards, rewards_in_arena)
            key, _ = split(key)

            trial_data, weights, rewards_in_arena, r_history = simulate_fly_trial(
                key,
                weights,
                plasticity_coeffs,
                plasticity_func,
                rewards_in_arena,
                r_history,
                odor_mus,
                odor_sigmas,
            )
            (
                xs[block][trial],
                odors[block][trial],
                sampled_ys[block][trial],
                rewards[block][trial],
                expected_rewards[block][trial],
            ) = trial_data

    return xs, odors, sampled_ys, rewards, expected_rewards


def simulate_insilico_experiment(
    initial_weights, plasticity_coeffs, plasticity_func, xs, rewards, expected_rewards, trial_lengths
):
    def step(weights, stimulus):
        x, reward, expected_reward, trial_length = stimulus
        return network_step(
            x,
            weights,
            plasticity_coeffs,
            plasticity_func,
            reward,
            expected_reward,
            trial_length
        )

    final_weights, logits = jax.lax.scan(
        step, initial_weights, (xs, rewards, expected_rewards, trial_lengths)
    )
    return jnp.squeeze(logits), final_weights


def network_step(
    input, weights, plasticity_coeffs, plasticity_func, reward, expected_reward, trial_length
):
    vmapped_forward = vmap(lambda weights, x: jnp.dot(x,weights), (None, 0))
    logits = vmapped_forward(weights, input)
    x = input[trial_length - 1]
    dw = weight_update(
        x, weights, plasticity_coeffs, plasticity_func, reward, expected_reward
    )
    weights += dw

    return weights, logits


def weight_update(x, weights, plasticity_coeffs, plasticity_func, reward, expected_reward):
    reward_term = reward - expected_reward
    m, n = weights.shape
    in_grid, _ = jnp.meshgrid(
        reshape(x, (m,)),
        jnp.ones(
            n,
        ),
        indexing="ij",
    )

    vfun = vmap(plasticity_func, in_axes=(0, None, 0, None))
    dw = vmap(vfun, in_axes=(1, None, 1, None), out_axes=1)(
        in_grid, reward_term, weights, plasticity_coeffs
    )

    assert (
        dw.shape == weights.shape
    ), "dw and w should be of the same shape to prevent broadcasting while adding"

    return dw


def truncated_sigmoid(x):
    epsilon = 1e-6
    return jnp.clip(jax.nn.sigmoid(x), epsilon, 1 - epsilon)
