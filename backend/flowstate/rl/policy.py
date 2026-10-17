"""Parameter-shared actor-critic for the junction grid.

The plain SB3 MLP sees all four junctions as one 76-dimensional vector and must learn four separate input-output
maps. Here the *same* small network reads every junction's own observation block (its approaches, its signal, and its
neighbours' queue ratios) and produces that junction's hold/switch logits, so every simulated step gives four
training samples for one set of weights and the symmetry between junctions is built in. The critic pools the
junction embeddings.

Observations stay raw (no wrapper), so a saved policy loads with ``PPO.load`` alone.
"""

from __future__ import annotations

import torch
from torch import nn
from stable_baselines3.common.policies import ActorCriticPolicy

from .config import CONTROLLED
from .env import OBS_PER_JUNCTION

N_J = len(CONTROLLED)
HOLD_BIAS = 0.0
SWITCH_BIAS = -1.5  # start at roughly 18 % switch probability per decision instead of 50 %: long, fixed-time-like greens first


class _SharedExtractor(nn.Module):
    def __init__(self, hidden: int):
        super().__init__()
        self.latent_dim_pi = 2 * N_J
        self.latent_dim_vf = hidden
        self.enc = nn.Sequential(nn.Linear(OBS_PER_JUNCTION, hidden), nn.Tanh(), nn.Linear(hidden, hidden), nn.Tanh())
        self.ctx = nn.Sequential(nn.Linear(2 * hidden, hidden), nn.Tanh())  # junction embedding + mean of all junctions
        self.pi_head = nn.Linear(hidden, 2)
        self.vf = nn.Sequential(nn.Linear(hidden, hidden), nn.Tanh())

    def _embed(self, obs: torch.Tensor) -> torch.Tensor:
        h = self.enc(obs.view(-1, N_J, OBS_PER_JUNCTION))  # (B, J, H)
        return self.ctx(torch.cat([h, h.mean(1, keepdim=True).expand_as(h)], dim=-1))

    def forward(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        return self.forward_actor(obs), self.forward_critic(obs)

    def forward_actor(self, obs: torch.Tensor) -> torch.Tensor:
        return self.pi_head(self._embed(obs)).reshape(obs.shape[0], 2 * N_J)

    def forward_critic(self, obs: torch.Tensor) -> torch.Tensor:
        return self.vf(self._embed(obs).mean(1))


class SharedJunctionPolicy(ActorCriticPolicy):
    def __init__(self, *args, hidden: int = 64, **kwargs):
        self._hidden = hidden
        kwargs.pop("net_arch", None)
        super().__init__(*args, **kwargs)
        self.action_net = nn.Identity()  # the extractor already emits the 2 x J logits
        head = self.mlp_extractor.pi_head  # SB3's orthogonal init has just run over the extractor; set the prior afterwards
        with torch.no_grad():
            head.weight.mul_(0.01)
            head.bias.copy_(torch.tensor([HOLD_BIAS, SWITCH_BIAS]))

    def _build_mlp_extractor(self) -> None:
        self.mlp_extractor = _SharedExtractor(self._hidden)

    def _get_constructor_parameters(self):
        d = super()._get_constructor_parameters()
        d["hidden"] = self._hidden
        return d
