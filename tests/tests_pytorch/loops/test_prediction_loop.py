# Copyright The Lightning AI team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import itertools

import pytest
from torch.utils.data import DataLoader, DistributedSampler, SequentialSampler

from lightning.pytorch import Trainer
from lightning.pytorch.demos.boring_classes import BoringModel, RandomDataset
from lightning.pytorch.overrides.distributed import _IndexBatchSamplerWrapper


def test_prediction_loop_stores_predictions(tmp_path):
    class MyModel(BoringModel):
        def predict_step(self, batch, batch_idx):
            return batch_idx

    model = MyModel()
    trainer = Trainer(
        default_root_dir=tmp_path,
        limit_predict_batches=2,
        logger=False,
        enable_progress_bar=False,
        enable_model_summary=False,
    )
    predictions = trainer.predict(model, return_predictions=True)
    assert predictions == [0, 1]
    # the predictions are still available
    assert trainer.predict_loop.predictions == predictions

    trainer = Trainer(
        default_root_dir=tmp_path,
        limit_predict_batches=2,
        logger=False,
        enable_progress_bar=False,
        enable_model_summary=False,
    )
    predictions = trainer.predict(model, return_predictions=False)
    assert predictions is None
    assert trainer.predict_loop.predictions == []


@pytest.mark.parametrize("use_distributed_sampler", (False, True))
def test_prediction_loop_batch_sampler_set_epoch_called(tmp_path, use_distributed_sampler):
    """Tests that set_epoch is called on the dataloader's batch sampler (if any) during prediction."""
    trainer = Trainer(
        default_root_dir=tmp_path,
        limit_predict_batches=1,
        enable_model_summary=False,
        enable_checkpointing=False,
        logger=False,
        strategy="ddp",
        devices=1,
        accelerator="cpu",
        use_distributed_sampler=use_distributed_sampler,
    )

    class MyModel(BoringModel):
        def predict_dataloader(self):
            dataset = RandomDataset(32, 64)
            sampler = None
            if not use_distributed_sampler:
                sampler = DistributedSampler(dataset)
            return DataLoader(dataset, sampler=sampler)

    model = MyModel()
    trainer.fit_loop.epoch_progress.current.processed = 2
    trainer.predict(model)

    # torch will set this .sampler attribute for backwards compatibility, but in reality, the batch sampler is used
    assert isinstance(trainer.predict_dataloaders.sampler, SequentialSampler)
    batch_sampler = trainer.predict_dataloaders.batch_sampler
    assert isinstance(batch_sampler, _IndexBatchSamplerWrapper)
    assert isinstance(batch_sampler.sampler, DistributedSampler)
    assert batch_sampler.sampler.epoch == 2


def test_prediction_loop_with_iterable_dataset(tmp_path):
    class MyModel(BoringModel):
        def predict_step(self, batch, batch_idx, dataloader_idx=0):
            return (batch, batch_idx, dataloader_idx)

    model = MyModel()
    trainer = Trainer(
        default_root_dir=tmp_path,
        limit_predict_batches=3,
        enable_model_summary=False,
        enable_checkpointing=False,
        logger=False,
    )
    preds = trainer.predict(model, itertools.count())
    assert preds == [(0, 0, 0), (1, 1, 0), (2, 2, 0)]

    preds = trainer.predict(model, [itertools.count(), itertools.count()])
    assert preds == [[(0, 0, 0), (1, 1, 0), (2, 2, 0)], [(0, 0, 1), (1, 1, 1), (2, 2, 1)]]

    preds = trainer.predict(model, {"a": [0, 1], "b": [2, 3]})
    assert preds == [[(0, 0, 0), (1, 1, 0)], [(2, 0, 1), (3, 1, 1)]]

    preds = trainer.predict(model, [[0, 1], [2, 3]])
    assert preds == [[(0, 0, 0), (1, 1, 0)], [(2, 0, 1), (3, 1, 1)]]

    class MyModel(BoringModel):
        def predict_step(self, dataloader_iter, batch_idx, dataloader_idx=0):
            ...

    model = MyModel()
    with pytest.raises(NotImplementedError, match="dataloader_iter.*is not supported with multiple dataloaders"):
        trainer.predict(model, {"a": [0, 1], "b": [2, 3]})