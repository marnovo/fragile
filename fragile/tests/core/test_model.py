import pytest  # noqa: F401

import numpy

from fragile.core.models import (
    BaseCritic,
    BaseModel,
    BinarySwap,
    Bounds,
    ContinuousModel,
    ContinousUniform,
    DiscreteModel,
    DiscreteUniform,
    NormalContinuous,
    _DtModel,
)
from fragile.core.states import States


def create_model(name="discrete"):
    if name == "discrete":
        return lambda: DiscreteUniform(n_actions=10)
    elif name == "continuous":
        bs = Bounds(low=-1, high=1, shape=(3,))
        return lambda: ContinousUniform(bounds=bs)
    elif name == "random_normal":
        bs = Bounds(low=-1, high=1, shape=(3,))
        return lambda: NormalContinuous(loc=0, scale=1, bounds=bs)
    raise ValueError("Invalid param `name`.")


@pytest.fixture(scope="module")
def model_fixture(request):
    return create_model(request.param)()


def create_model_states(model: BaseModel, batch_size: int = 10):
    return States(batch_size=batch_size, state_dict=model.get_params_dict())


class TestModel:
    model_fixture_params = ["discrete", "continuous", "random_normal"]

    @pytest.mark.parametrize("model_fixture", model_fixture_params, indirect=True)
    def test_get_params_dict(self, model_fixture):
        params_dict = model_fixture.get_params_dict()
        assert isinstance(params_dict, dict)
        for k, v in params_dict.items():
            assert isinstance(k, str)
            assert isinstance(v, dict)
            for ki, vi in v.items():
                assert isinstance(ki, str)

    @pytest.mark.parametrize("model_fixture", model_fixture_params, indirect=True)
    def test_reset(self, model_fixture):
        batch_size = 7
        states = model_fixture.reset(batch_size=batch_size)
        assert isinstance(states, model_fixture.STATE_CLASS)
        model_states = create_model_states(model=model_fixture, batch_size=batch_size)
        states = model_fixture.reset(batch_size=batch_size, model_states=model_states)
        assert isinstance(states, model_fixture.STATE_CLASS)
        assert len(model_states.actions) == batch_size

    @pytest.mark.parametrize("model_fixture", model_fixture_params, indirect=True)
    def test_predict(self, model_fixture):
        batch_size = 7
        states = create_model_states(model=model_fixture, batch_size=batch_size)
        updated_states = model_fixture.predict(model_states=states, env_states=states)
        assert isinstance(updated_states, model_fixture.STATE_CLASS)
        assert len(updated_states) == batch_size
        updated_states = model_fixture.predict(
            model_states=states, env_states=states, batch_size=batch_size
        )
        assert isinstance(updated_states, model_fixture.STATE_CLASS)
        assert len(updated_states) == batch_size
        if hasattr(model_fixture, "bounds"):
            assert model_fixture.bounds.points_in_bounds(updated_states.actions).all()
        with pytest.raises(ValueError):
            model_fixture.predict()


class DummyCritic(BaseCritic):
    def get_params_dict(self):
        return {"dt": {"dtype": float}}

    def calculate(
        self,
        batch_size: int = None,
        model_states: States = None,
        env_states: States = None,
        walkers_states: "StatesWalkers" = None,
    ) -> numpy.ndarray:
        batch_size = batch_size or env_states.n
        return 5 * numpy.ones(batch_size)


class TestDtModel:
    def test_get_params_dict_content(self):
        params = _DtModel().get_params_dict()
        assert "dt" in params
        assert "dtype" in params["dt"]
        assert params["dt"]["dtype"] == numpy.int_

    def test_override_get_params_dict(self):
        critic = DummyCritic()
        model = _DtModel(critic=critic)
        params = model.get_params_dict(override_params=False)
        assert "dt" in params
        assert "dtype" in params["dt"]
        assert params["dt"]["dtype"] == numpy.int_

        params = model.get_params_dict(override_params=True)
        assert "dt" in params
        assert "dtype" in params["dt"]
        assert params["dt"]["dtype"] == float


class DummyEnv:
    n_actions = 10


class TestDiscreteModel:
    def test_init(self):
        env_1 = DiscreteModel(n_actions=10)
        assert env_1.n_actions == 10
        env_2 = DiscreteModel(env=DummyEnv())
        assert env_2.n_actions == 10

    def test_get_params_dict(self):
        params = DiscreteModel(n_actions=10).get_params_dict()
        assert "actions" in params
        assert "dtype" in params["actions"]
        assert params["actions"]["dtype"] == numpy.int_


class TestDiscreteUniform:
    @pytest.mark.parametrize("n_actions", [2, 5, 10, 20])
    def test_sample(self, n_actions):
        model = DiscreteUniform(n_actions=n_actions)
        model_states = model.predict(batch_size=1000)
        actions = model_states.actions
        assert len(actions.shape) == 1
        assert len(numpy.unique(actions)) <= n_actions
        assert all(actions >= 0)
        assert all(actions <= n_actions)
        assert "dt" in model_states.keys()
        assert isinstance(model_states.dt, numpy.ndarray)
        assert (model_states.dt == 1).all(), model_states.dt

        states = create_model_states(batch_size=100, model=model)
        model_states = model.sample(batch_size=states.n, model_states=states)
        actions = model_states.actions
        assert len(actions.shape) == 1
        assert len(numpy.unique(actions)) <= n_actions
        assert all(actions >= 0)
        assert all(actions <= n_actions)
        assert numpy.allclose(actions, actions.astype(int))
        assert "dt" in model_states.keys()
        assert (model_states.dt == 1).all()

    @pytest.mark.parametrize("n_actions", [2, 5, 10, 20])
    def test_sample_with_critic(self, n_actions):
        model = DiscreteUniform(n_actions=n_actions, critic=DummyCritic())
        model_states = model.predict(batch_size=1000)
        actions = model_states.actions
        assert len(actions.shape) == 1
        assert len(numpy.unique(actions)) <= n_actions
        assert all(actions >= 0)
        assert all(actions <= n_actions)
        assert "dt" in model_states.keys()
        assert (model_states.dt == 5).all()

        states = create_model_states(batch_size=100, model=model)
        model_states = model.sample(batch_size=states.n, model_states=states)
        actions = model_states.actions
        assert len(actions.shape) == 1
        assert len(numpy.unique(actions)) <= n_actions
        assert all(actions >= 0)
        assert all(actions <= n_actions)
        assert numpy.allclose(actions, actions.astype(int))
        assert "dt" in model_states.keys()
        assert (model_states.dt == 5).all()


class TestBinarySwap:
    def test_sample(self):
        model = BinarySwap(n_actions=10, n_swaps=3)
        states = model.predict(batch_size=10)
        actions = states.actions
        vectors = actions.sum(axis=1)
        assert actions.min() == 0
        assert actions.max() == 1
        assert (vectors > 0).all(), actions
        assert (vectors <= 3).all(), actions


class TestContinuousModel:
    def test_attributes(self):
        bounds = Bounds(low=-1, high=3, shape=(3,))
        model = ContinuousModel(bounds=bounds)
        assert model.shape == (3,)
        assert model.n_dims == 3

    def test_get_params_dict(self):
        bounds = Bounds(low=-1, high=3, shape=(3,))
        model = ContinuousModel(bounds=bounds)
        params = model.get_params_dict()
        assert params["actions"]["size"] == model.shape


class TestContinuousUniform:
    def test_sample(self):
        bounds = Bounds(low=-1, high=3, shape=(3,))
        model = ContinousUniform(bounds=bounds)
        actions = model.predict(batch_size=100).actions
        assert actions.min() >= -1
        assert actions.max() <= 3

        bounds = Bounds(low=-1, high=3, shape=(3, 10))
        model = ContinousUniform(bounds=bounds)
        actions = model.predict(batch_size=100).actions
        assert actions.min() >= -1
        assert actions.max() <= 3


class TestRandomNormal:
    def test_sample(self):
        bounds = Bounds(low=-5, high=5, shape=(3,))
        model = NormalContinuous(bounds=bounds)
        actions = model.predict(batch_size=10000).actions
        assert actions.min() >= -5
        assert actions.max() <= 5
        assert numpy.allclose(actions.mean(), 0, atol=0.05)
        assert numpy.allclose(actions.std(), 1, atol=0.05)

        bounds = Bounds(low=-10, high=30, shape=(3, 10))
        model = NormalContinuous(bounds=bounds, loc=5, scale=2)
        actions = model.predict(batch_size=10000).actions
        assert actions.min() >= -10
        assert actions.max() <= 30
        assert numpy.allclose(actions.mean(), 5, atol=0.05), actions.mean()
        assert numpy.allclose(actions.std(), 2, atol=0.05), actions.std()
