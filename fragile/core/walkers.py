import copy
from typing import Callable, Dict, List, Optional, Set, Tuple

# import line_profiler
import numpy

from fragile.core.base_classes import BaseCritic, BaseWalkers
from fragile.core.states import StatesEnv, StatesModel, StatesWalkers
from fragile.core.utils import float_type, relativize, Scalar, StateDict, statistics_from_array


class SimpleWalkers(BaseWalkers):
    """
    This class is in charge of performing all the mathematical operations involved in evolving a \
    cloud of walkers.

    """

    STATE_CLASS = StatesWalkers

    def __init__(
        self,
        n_walkers: int,
        env_state_params: StateDict,
        model_state_params: StateDict,
        reward_scale: float = 1.0,
        dist_scale: float = 1.0,
        max_iters: int = None,
        accumulate_rewards: bool = True,
        distance_function: Optional[
            Callable[[numpy.ndarray, numpy.ndarray], numpy.ndarray]
        ] = None,
        ignore_clone: Optional[Dict[str, Set[str]]] = None,
        **kwargs
    ):
        """
        Initialize a new `Walkers` instance.

        Args:
            n_walkers: Number of walkers of the instance.
            env_state_params: Dictionary to instantiate the States of an :class:`Environment`.
            model_state_params: Dictionary to instantiate the States of a :class:`Model`.
            reward_scale: Regulates the importance of the reward. Recommended to \
                          keep in the [0, 5] range. Higher values correspond to \
                          higher importance.
            dist_scale: Regulates the importance of the distance. Recommended to \
                          keep in the [0, 5] range. Higher values correspond to \
                          higher importance.
            max_iters: Maximum number of iterations that the walkers are allowed \
                       to perform.
            accumulate_rewards: If ``True`` the rewards obtained after transitioning \
                                to a new state will accumulate. If ``False`` only the last \
                                reward will be taken into account.
            distance_function: Function to compute the distances between two \
                               groups of walkers. It will be applied row-wise \
                               to the walkers observations and it will return a \
                               vector of scalars. Defaults to l2 norm.
            ignore_clone: Dictionary containing the attribute values that will \
                          not be cloned. Its keys can be be either "env", of \
                          "model", to reference the `env_states` and the \
                          `model_states`. Its values are a set of string with \
                          the names of the attributes that will not be cloned.
            kwargs: Additional attributes stored in the :class:`StatesWalkers`.

        """
        super(SimpleWalkers, self).__init__(
            n_walkers=n_walkers,
            env_state_params=env_state_params,
            model_state_params=model_state_params,
            accumulate_rewards=accumulate_rewards,
        )

        def l2_norm(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
            return numpy.linalg.norm(x - y, axis=1)

        self._model_states = StatesModel(state_dict=model_state_params, batch_size=n_walkers)
        self._env_states = StatesEnv(state_dict=env_state_params, batch_size=n_walkers)
        self._states = self.STATE_CLASS(batch_size=n_walkers, **kwargs)
        self.distance_function = distance_function if distance_function is not None else l2_norm
        self.reward_scale = reward_scale
        self.dist_scale = dist_scale
        self.n_iters = 0
        self.max_iters = max_iters if max_iters is not None else 1e12
        self._id_counter = 0
        self.ignore_clone = ignore_clone if ignore_clone is not None else {}

    def __repr__(self) -> str:
        """Print all the data involved in the current run of the algorithm."""
        try:
            text = self._print_stats()
            text += "Walkers States: {}\n".format(self._repr_state(self._states))
            text += "Env States: {}\n".format(self._repr_state(self._env_states))
            text += "Model States: {}\n".format(self._repr_state(self._model_states))
            return text
        except Exception:
            return super(SimpleWalkers, self).__repr__()

    def _print_stats(self) -> str:
        """Print several statistics of the current state of the swarm."""
        text = "{} iteration {} Dead walkers: {:.2f}% Cloned: {:.2f}%\n\n".format(
            self.__class__.__name__,
            self.n_iters,
            100 * self.states.end_condition.sum() / self.n,
            100 * self.states.will_clone.sum() / self.n,
        )
        return text

    def ids(self) -> List[int]:
        """
        Return a list of unique ids for each walker state.

        The returned ids are integers representing the hash of the different states.
        """
        # ids = numpy.arange(self.n) + self._id_counter
        # self._id_counter += self.n
        return self.env_states.hash_values("states")

    def update_ids(self):
        """Update the unique id of each walker and store it in the :class:`StatesWalkers`."""
        self.states.update(id_walkers=self.ids().copy())

    @property
    def states(self) -> StatesWalkers:
        """Return the `StatesWalkers` class that contains the data used by the instance."""
        return self._states

    @property
    def env_states(self) -> StatesEnv:
        """Return the `States` class that contains the data used by the :class:`Environment`."""
        return self._env_states

    @property
    def model_states(self) -> StatesModel:
        """Return the `States` class that contains the data used by a Model."""
        return self._model_states

    def calculate_end_condition(self) -> bool:
        """
        Process data from the current state to decide if the iteration process should stop.

        Returns:
            Boolean indicating if the iteration process should be finished. ``True`` means \
            it should be stopped, and ``False`` means it should continue.

        """
        all_dead = self.states.end_condition.sum() == self.n
        max_iters = self.n_iters >= self.max_iters
        return all_dead or max_iters

    # @profile
    def calculate_distances(self) -> None:
        """Calculate the corresponding distance function for each observation with \
        respect to another observation chosen at random.

        The internal :class:`StateWalkers` is updated with the relativized distance values.
        """
        compas_ix = numpy.random.permutation(numpy.arange(self.n))  # self.get_alive_compas()
        obs = self.env_states.observs.reshape(self.n, -1)
        distances = self.distance_function(obs, obs[compas_ix])
        distances = relativize(distances.flatten())
        self.update_states(distances=distances, compas_dist=compas_ix)

    def calculate_virtual_reward(self) -> None:
        """
        Calculate the virtual reward and update the internal state.

        The cumulative_reward is transformed with the relativize function. \
        The distances stored in the :class:`StatesWalkers` are already transformed.
        """
        processed_rewards = relativize(self.states.cum_rewards)
        virt_rw = processed_rewards ** self.reward_scale * self.states.distances ** self.dist_scale
        self.update_states(virtual_rewards=virt_rw, processed_rewards=processed_rewards)

    def get_alive_compas(self) -> numpy.ndarray:
        """
        Return the indexes of alive companions chosen at random.

        Returns:
            Numpy array containing the int indexes of alive walkers chosen at \
            random with replacement. Its length is equal to the number of walkers.

        """
        self.states.alive_mask = numpy.logical_not(self.states.end_condition)
        if not self.states.alive_mask.any():  # No need to sample if all walkers are dead.
            return numpy.arange(self.n)
        compas_ix = numpy.arange(self.n)[self.states.alive_mask]
        compas = self.random_state.choice(compas_ix, self.n, replace=True)
        compas[: len(compas_ix)] = compas_ix
        return compas

    def update_clone_probs(self) -> None:
        """
        Calculate the new probability of cloning for each walker.

        Updates the :class:`StatesWalkers` with both the probability of cloning \
        and the index of the randomly chosen companions that were selected to \
        compare the virtual rewards.
        """
        all_virtual_rewards_are_equal = (
            self.states.virtual_rewards == self.states.virtual_rewards[0]
        ).all()
        if all_virtual_rewards_are_equal:
            clone_probs = numpy.zeros(self.n, dtype=float_type)
            compas_ix = numpy.arange(self.n)
        else:
            compas_ix = self.get_alive_compas()
            companions = self.states.virtual_rewards[compas_ix]
            # This value can be negative!!
            clone_probs = (companions - self.states.virtual_rewards) / self.states.virtual_rewards
            # clone_probs = numpy.sqrt(numpy.clip(clone_probs, 0, 1.1))
        self.update_states(clone_probs=clone_probs, compas_clone=compas_ix)

    # @profile
    def balance(self) -> Tuple[set, set]:
        """
        Perform an iteration of the FractalAI algorithm for balancing the \
        walkers distribution.

        It performs the necessary calculations to determine which walkers will clone, \
        and performs the cloning process.

        Returns:
            A tuple containing two sets: The first one represent the unique ids \
            of the states for each walker at the start of the iteration. The second \
            one contains the ids of the states after the cloning process.

        """
        old_ids = set(self.states.id_walkers.copy())
        self.calculate_distances()
        self.calculate_virtual_reward()
        self.update_clone_probs()
        self.clone_walkers()
        new_ids = set(self.states.id_walkers.copy())
        return old_ids, new_ids

    # @profile
    def clone_walkers(self) -> None:
        """
        Sample the clone probability distribution and clone the walkers accordingly.

        This function will update the internal :class:`StatesWalkers`, \
        :class:`StatesEnv`, and :class:`StatesModel`.
        """
        will_clone = self.states.clone_probs > self.random_state.random_sample(self.n)
        will_clone[self.states.end_condition] = True  # Dead walkers always clone
        self.update_states(will_clone=will_clone)
        clone, compas = self.states.clone()
        self._env_states.clone(
            will_clone=clone, compas_ix=compas, ignore=self.ignore_clone.get("env")
        )
        self._model_states.clone(
            will_clone=clone, compas_ix=compas, ignore=self.ignore_clone.get("model")
        )

    def reset(
        self,
        env_states: StatesEnv = None,
        model_states: StatesModel = None,
        walkers_states: StatesWalkers = None,
    ) -> None:
        """
        Restart all the internal states involved in the algorithm iteration.

        After reset a new run of the algorithm will be ready to be launched.
        """
        if walkers_states is not None:
            self.states.update(walkers_states)
        else:
            self.states.reset()
        self.update_states(env_states=env_states, model_states=model_states)
        self.n_iters = 0

    def update_states(
        self, env_states: StatesEnv = None, model_states: StatesModel = None, **kwargs
    ):
        """
        Update the States variables that do not contain internal data and \
        accumulate the rewards in the internal states if applicable.

        Args:
            env_states: States containing the data associated with the Environment.
            model_states: States containing data associated with the Environment.
            **kwargs: Internal states will be updated via keyword arguments.

        """
        if kwargs:
            if kwargs.get("rewards") is not None:
                self._accumulate_and_update_rewards(kwargs["rewards"])
                del kwargs["rewards"]
            self.states.update(**kwargs)
        if isinstance(env_states, StatesEnv):
            self._env_states.update(env_states)
            if hasattr(env_states, "rewards"):
                self._accumulate_and_update_rewards(env_states.rewards)
        if isinstance(model_states, StatesModel):
            self._model_states.update(model_states)

    def _accumulate_and_update_rewards(self, rewards: numpy.ndarray):
        """
        Use as reward either the sum of all the rewards received during the \
        current run, or use the last reward value received as reward.

        Args:
            rewards: Array containing the last rewards received by every walker.
        """
        if self._accumulate_rewards:
            if not isinstance(self.states.get("cum_rewards"), numpy.ndarray):
                cum_rewards = numpy.zeros(self.n)
            else:
                cum_rewards = self.states.cum_rewards
            cum_rewards = cum_rewards + rewards
        else:
            cum_rewards = rewards
        self.update_states(cum_rewards=cum_rewards)

    @staticmethod
    def _repr_state(state):
        string = "\n"
        for k, v in state.items():
            if k in ["observs", "states"]:
                continue
            shape = v.shape if hasattr(v, "shape") else None
            new_str = "{} shape {} Mean: {:.3f}, Std: {:.3f}, Max: {:.3f} Min: {:.3f}\n".format(
                k, shape, *statistics_from_array(v)
            )
            string += new_str
        return string

    def fix_best(self):
        """Ensure the best state found is assigned to the last walker fo the swarm."""
        pass


class Walkers(SimpleWalkers):
    """
    The Walkers is a data structure that takes care of all the data involved \
    in making a Swarm evolve.
    """

    def __init__(
        self,
        critic: BaseCritic = None,
        minimize: bool = False,
        best_walker: Tuple[numpy.ndarray, Scalar] = None,
        *args,
        **kwargs
    ):
        """
        Initialize a :class:`Walkers`.

        Args:
            critic: critic that will be used to calculate custom rewards.
            minimize: If ``True`` the algorithm will perform a minimization \
                      process. If ``False`` it will be a maximization process.
            best_walker: Tuple containing the best state and reward that will \
                        be used as the initial best values found.
            *args: Passed to :class:`SimpleWalkers`.
            **kwargs: Passed to :class:`SimpleWalkers`.
        """
        # Add data specific to the child class in the StatesWalkers class as new attributes.
        kwargs["critic_score"] = kwargs.get("critic_score", numpy.zeros(kwargs["n_walkers"]))
        self.dtype = float_type
        best_state, best_obs, best_reward = (
            best_walker if best_walker is not None else (None, None, -numpy.inf)
        )
        super(Walkers, self).__init__(
            best_reward=best_reward, best_obs=best_obs, best_state=best_state, *args, **kwargs
        )
        self.critic = critic
        self.minimize = minimize
        self.efficiency = 0
        self._min_entropy = 0

    def __repr__(self):
        text = "\nBest reward found: {:.4f} , efficiency {:.3f}, Critic: {}\n".format(
            float(self.states.best_reward), self.efficiency, self.critic
        )
        return text + super(Walkers, self).__repr__()

    # @profile
    def calculate_virtual_reward(self):
        """Apply the virtual reward formula to account for all the different goal scores."""
        rewards = -1 * self.states.cum_rewards if self.minimize else self.states.cum_rewards
        processed_rewards = relativize(rewards)
        score_reward = processed_rewards ** self.reward_scale
        score_dist = self.states.distances ** self.dist_scale
        virt_rw = score_reward * score_dist
        dist_prob = score_dist / score_dist.sum()
        reward_prob = score_reward / score_reward.sum()
        total_entropy = numpy.prod(2 - dist_prob ** reward_prob)
        self._min_entropy = numpy.prod(2 - reward_prob ** reward_prob)
        self.efficiency = self._min_entropy / total_entropy
        self.update_states(virtual_rewards=virt_rw, processed_rewards=processed_rewards)
        if self.critic is not None:
            critic_states = self.critic.calculate(
                walkers_states=self.states,
                model_states=self.model_states,
                env_states=self.env_states,
            )
            self.states.update(other=critic_states)
            virt_rew = self.states.virtual_rewards * self.states.critic
        else:
            virt_rew = self.states.virtual_rewards
        self.states.update(virtual_rewards=virt_rew)

    # @profile
    def balance(self):
        """Perform FAI iteration to clone the states."""
        self.update_best()
        returned = super(Walkers, self).balance()
        if self.critic is not None:
            critic_states = self.critic.update(
                walkers_states=self.states,
                model_states=self.model_states,
                env_states=self.env_states,
            )
            self.states.update(other=critic_states)
        return returned

    def _get_best_index(self):
        rewards = self.states.cum_rewards[numpy.logical_not(self.states.end_condition)]
        if len(rewards) == 0:
            return 0
        best = rewards.min() if self.minimize else rewards.max()
        idx = (self.states.cum_rewards == best).astype(int)
        ix = idx.argmax()  # if self.minimize else idx.argmax()
        return ix

    def update_best(self):
        """Keep track of the best state found and its reward."""
        ix = self._get_best_index()
        best_obs = self.env_states.observs[ix].copy()
        best_reward = float(self.states.cum_rewards[ix])
        best_state = self.env_states.states[ix].copy()
        best_is_alive = not bool(self.env_states.ends[ix])
        has_improved = (
            self.states.best_reward > best_reward
            if self.minimize
            else self.states.best_reward < best_reward
        )
        if has_improved and best_is_alive:
            self.states.update(
                best_reward=best_reward,
                best_state=best_state,
                best_obs=best_obs,
                best_id=int(self.states.id_walkers[ix]),
            )
            self.states.update()

    def fix_best(self):
        """Ensure the best state found is assigned to the last walker fo the swarm."""
        if self.states.best_reward is not None:
            self.env_states.observs[-1] = self.states.best_obs.copy()
            self.states.cum_rewards[-1] = float(self.states.best_reward)
            self.states.id_walkers[-1] = copy.copy(self.states.best_id)
            self.env_states.states[-1] = self.states.best_state.copy()

    def reset(
        self,
        env_states: StatesEnv = None,
        model_states: StatesModel = None,
        walkers_states: StatesWalkers = None,
    ):
        """
        Reset a :class:`Walkers` and clear the internal data to start a \
        new search process.

        Restart all the variables needed to perform the fractal evolution process.

        Args:
            model_states: :class:`StatesModel` that define the initial state of the environment.
            env_states: :class:`StatesEnv` that define the initial state of the model.
            walkers_states: :class:`StatesWalkers` that define the internal states of the walkers.

        """
        super(Walkers, self).reset(
            env_states=env_states, model_states=model_states, walkers_states=walkers_states
        )
        rewards = self.env_states.rewards
        ix = rewards.argmin() if self.minimize else rewards.argmax()
        self.states.update()
        self.states.update(
            best_reward=numpy.inf if self.minimize else -numpy.inf,
            best_obs=copy.deepcopy(self.env_states.observs[ix]),
            best_state=copy.deepcopy(self.env_states.states[ix]),
        )
        if self.critic is not None:
            critic_score = self.critic.reset(
                env_states=self.env_states, model_states=model_states, walker_states=walkers_states
            )
            self.states.update(critic_score=critic_score)
