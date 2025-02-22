# coding=utf-8
# Copyright 2021 The HuggingFace Inc. team. All rights reserved.
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
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring
# pylint: disable=unused-variable
# pylint: disable=unused-argument
# pylint: disable=redefined-builtin
# pylint: disable=invalid-name
# pylint: disable=consider-using-enumerate
""" Testing suite for the Mindspore Hubert model. """

import math
import unittest
import pytest
import numpy as np

from datasets import load_dataset

import mindspore as ms
import mindspore.ops as F
import mindspore.numpy as mnp
from mindspore import Tensor

from mindnlp.transformers import HubertConfig
from mindnlp.transformers import (
    HubertForCTC,
    HubertForSequenceClassification,
    HubertModel,
    Wav2Vec2FeatureExtractor,
    Wav2Vec2Processor,
)
from mindnlp.transformers.models.hubert.modeling_hubert import _compute_mask_indices
from mindnlp.utils.testing_utils import (
    is_mindspore_available,
    require_mindspore,
    slow,
)

from ...test_configuration_common import ConfigTester
from ...test_modeling_common import (
    ModelTesterMixin,
    _config_zero_init,
    floats_tensor,
    ids_tensor,
    random_attention_mask,
)

mnp.allclose = lambda x, y, *args, **kwargs: np.allclose(x.asnumpy(), y.asnumpy(), *args, **kwargs)


class HubertModelTester:
    def __init__(
        self,
        parent,
        batch_size=13,
        seq_length=1024,  # speech is longer
        is_training=False,
        hidden_size=16,
        feat_extract_norm="group",
        feat_extract_dropout=0.0,
        feat_extract_activation="gelu",
        conv_dim=(32, 32, 32),
        conv_stride=(4, 4, 4),
        conv_kernel=(8, 8, 8),
        conv_bias=False,
        num_conv_pos_embeddings=16,
        num_conv_pos_embedding_groups=2,
        num_hidden_layers=2,
        num_attention_heads=2,
        hidden_dropout_prob=0.1,  # this is most likely not correctly set yet
        intermediate_size=20,
        layer_norm_eps=1e-5,
        hidden_act="gelu",
        initializer_range=0.02,
        vocab_size=32,
        do_stable_layer_norm=False,
        scope=None,
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.hidden_size = hidden_size
        self.feat_extract_norm = feat_extract_norm
        self.feat_extract_dropout = feat_extract_dropout
        self.feat_extract_activation = feat_extract_activation
        self.conv_dim = conv_dim
        self.conv_stride = conv_stride
        self.conv_kernel = conv_kernel
        self.conv_bias = conv_bias
        self.num_conv_pos_embeddings = num_conv_pos_embeddings
        self.num_conv_pos_embedding_groups = num_conv_pos_embedding_groups
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.hidden_dropout_prob = hidden_dropout_prob
        self.intermediate_size = intermediate_size
        self.layer_norm_eps = layer_norm_eps
        self.hidden_act = hidden_act
        self.initializer_range = initializer_range
        self.vocab_size = vocab_size
        self.do_stable_layer_norm = do_stable_layer_norm
        self.scope = scope

        output_seq_length = self.seq_length
        for kernel, stride in zip(self.conv_kernel, self.conv_stride):
            output_seq_length = (output_seq_length - (kernel - 1)) / stride
        self.output_seq_length = int(math.ceil(output_seq_length))
        self.encoder_seq_length = self.output_seq_length

    def prepare_config_and_inputs(self):
        input_values = floats_tensor([self.batch_size, self.seq_length], scale=1.0)
        attention_mask = random_attention_mask([self.batch_size, self.seq_length])
        config = self.get_config()
        return config, input_values, attention_mask

    def get_config(self):
        return HubertConfig(
            hidden_size=self.hidden_size,
            feat_extract_norm=self.feat_extract_norm,
            feat_extract_dropout=self.feat_extract_dropout,
            feat_extract_activation=self.feat_extract_activation,
            conv_dim=self.conv_dim,
            conv_stride=self.conv_stride,
            conv_kernel=self.conv_kernel,
            conv_bias=self.conv_bias,
            num_conv_pos_embeddings=self.num_conv_pos_embeddings,
            num_conv_pos_embedding_groups=self.num_conv_pos_embedding_groups,
            num_hidden_layers=self.num_hidden_layers,
            num_attention_heads=self.num_attention_heads,
            hidden_dropout_prob=self.hidden_dropout_prob,
            intermediate_size=self.intermediate_size,
            layer_norm_eps=self.layer_norm_eps,
            hidden_act=self.hidden_act,
            initializer_range=self.initializer_range,
            vocab_size=self.vocab_size,
            do_stable_layer_norm=self.do_stable_layer_norm,
        )

    def create_and_check_model(self, config, input_values, attention_mask):
        model = HubertModel(config=config)
        model.set_train(False)
        result = model(input_values, attention_mask=attention_mask)
        self.parent.assertEqual(
            result.last_hidden_state.shape, (self.batch_size, self.output_seq_length, self.hidden_size)
        )

    def create_and_check_batch_inference(self, config, input_values, *args):
        # test does not pass for models making use of `group_norm`
        # check: https://github.com/pytorch/fairseq/issues/3227
        model = HubertModel(config=config)
        model.set_train(False)

        input_values = input_values[:3]
        attention_mask = F.ones(input_values.shape, dtype=ms.bool_)

        # pad input
        input_lengths = [input_values.shape[-1] // i for i in [4, 2, 1]]
        for i in range(len(input_lengths)):
            input_values[i, input_lengths[i] :] = 0.0
            attention_mask[i, input_lengths[i] :] = 0.0

        batch_outputs = model(input_values, attention_mask=attention_mask).last_hidden_state

        for i in range(input_values.shape[0]):
            input_slice = input_values[i : i + 1, : input_lengths[i]]
            output = model(input_slice).last_hidden_state

            batch_output = batch_outputs[i : i + 1, : output.shape[1]]
            self.parent.assertTrue(mnp.allclose(output, batch_output, atol=1e-3))

    def check_ctc_loss(self, config, input_values, *args):
        model = HubertForCTC(config=config)
        model.set_train(False) # make sure that dropout is disabled

        input_values = input_values[:3]
        attention_mask = F.ones(input_values.shape, dtype=ms.int64)

        input_lengths = [input_values.shape[-1] // i for i in [4, 2, 1]]
        max_length_labels = model._get_feat_extract_output_lengths(ms.tensor(input_lengths))
        labels = ids_tensor((input_values.shape[0], min(max_length_labels).item() - 1), model.config.vocab_size)

        # pad input
        for i in range(len(input_lengths)):
            input_values[i, input_lengths[i] :] = 0.0
            attention_mask[i, input_lengths[i] :] = 0

        model.config.ctc_loss_reduction = "sum"
        sum_loss = model(input_values, attention_mask=attention_mask, labels=labels).loss.item()

        model.config.ctc_loss_reduction = "mean"
        mean_loss = model(input_values, attention_mask=attention_mask, labels=labels).loss.item()

        self.parent.assertTrue(isinstance(sum_loss, float))
        self.parent.assertTrue(isinstance(mean_loss, float))

    def check_seq_classifier_loss(self, config, input_values, *args):
        model = HubertForSequenceClassification(config=config)
        model.set_train(False) # make sure that dropout is disabled

        input_values = input_values[:3]
        attention_mask = F.ones(input_values.shape, dtype=ms.int64)

        input_lengths = [input_values.shape[-1] // i for i in [4, 2, 1]]
        labels = ids_tensor((input_values.shape[0], 1), len(model.config.id2label))

        # pad input
        for i in range(len(input_lengths)):
            input_values[i, input_lengths[i] :] = 0.0
            attention_mask[i, input_lengths[i] :] = 0

        masked_loss = model(input_values, attention_mask=attention_mask, labels=labels).loss.item()
        unmasked_loss = model(input_values, labels=labels).loss.item()

        self.parent.assertTrue(isinstance(masked_loss, float))
        self.parent.assertTrue(isinstance(unmasked_loss, float))
        self.parent.assertTrue(masked_loss != unmasked_loss)

    def check_ctc_training(self, config, input_values, *args):
        config.ctc_zero_infinity = True
        model = HubertForCTC(config=config)
        model.set_train(True)

        # freeze feature encoder
        model.freeze_feature_encoder()

        input_values = input_values[:3]
        input_lengths = [input_values.shape[-1] // i for i in [4, 2, 1]]
        max_length_labels = model._get_feat_extract_output_lengths(ms.tensor(input_lengths))
        labels = ids_tensor((input_values.shape[0], max(max_length_labels).item() - 2), model.config.vocab_size)

        # pad input
        for i in range(len(input_lengths)):
            input_values[i, input_lengths[i] :] = 0.0

            if max_length_labels[i].item() < labels.shape[-1]:
                # it's important that we make sure that target lengths are at least
                # one shorter than logit lengths to prevent -inf
                labels[i, max_length_labels[i].item() - 1 :] = -100

        loss = model(input_values, labels=labels).loss
        self.parent.assertFalse(F.isinf(loss).item())

        # TODO: backward()

    def check_seq_classifier_training(self, config, input_values, *args):
        config.ctc_zero_infinity = True
        model = HubertForSequenceClassification(config=config)
        model.set_train(True)

        # freeze everything but the classification head
        model.freeze_base_model()

        input_values = input_values[:3]
        input_lengths = [input_values.shape[-1] // i for i in [4, 2, 1]]
        labels = ids_tensor((input_values.shape[0], 1), len(model.config.id2label))

        # pad input
        for i in range(len(input_lengths)):
            input_values[i, input_lengths[i] :] = 0.0

        loss = model(input_values, labels=labels).loss
        self.parent.assertFalse(F.isinf(loss).item())

        # TODO: backward()

    def check_labels_out_of_vocab(self, config, input_values, *args):
        model = HubertForCTC(config)
        model.set_train(True)

        input_values = input_values[:3]
        input_lengths = [input_values.shape[-1] // i for i in [4, 2, 1]]
        max_length_labels = model._get_feat_extract_output_lengths(ms.tensor(input_lengths))
        labels = ids_tensor((input_values.shape[0], max(max_length_labels).item() - 2), model.config.vocab_size + 100)

        with pytest.raises(ValueError):
            model(input_values, labels=labels)

    def prepare_config_and_inputs_for_common(self):
        config, input_values, attention_mask = self.prepare_config_and_inputs()
        inputs_dict = {"input_values": input_values, "attention_mask": attention_mask}
        return config, inputs_dict


@require_mindspore
class HubertModelTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (HubertForCTC, HubertForSequenceClassification, HubertModel) if is_mindspore_available() else ()
    pipeline_model_mapping = (
        {
            "audio-classification": HubertForSequenceClassification,
            "automatic-speech-recognition": HubertForCTC,
            "feature-extraction": HubertModel,
        }
    )
    fx_compatible = True
    test_pruning = False
    test_headmasking = False

    def setUp(self):
        self.model_tester = HubertModelTester(self)
        self.config_tester = ConfigTester(self, config_class=HubertConfig, hidden_size=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    def test_ctc_loss_inference(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_ctc_loss(*config_and_inputs)

    def test_seq_classifier_loss_inference(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_seq_classifier_loss(*config_and_inputs)

    @unittest.skip('ignore train temporarily')
    def test_ctc_train(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_ctc_training(*config_and_inputs)

    @unittest.skip('ignore train temporarily')
    def test_seq_classifier_train(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_seq_classifier_training(*config_and_inputs)

    def test_labels_out_of_vocab(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_labels_out_of_vocab(*config_and_inputs)

    # Hubert has no inputs_embeds
    def test_inputs_embeds(self):
        pass

    # `input_ids` is renamed to `input_values`
    def test_forward_signature(self):
        pass

    # Hubert cannot resize token embeddings
    # since it has no tokens embeddings
    def test_resize_tokens_embeddings(self):
        pass

    # Hubert has no inputs_embeds
    # and thus the `get_input_embeddings` fn
    # is not implemented
    def test_model_common_attributes(self):
        pass

    def test_initialization(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        configs_no_init = _config_zero_init(config)
        for model_class in self.all_model_classes:
            model = model_class(config=configs_no_init)
            for name, param in model.parameters_and_names():
                uniform_init_parms = [
                    "conv.weight",
                    "conv.parametrizations.weight",
                    "masked_spec_embed",
                    "quantizer.weight_proj.weight",
                ]
                if param.requires_grad:
                    if any(x in name for x in uniform_init_parms):
                        self.assertTrue(
                            -1.0 <= ((param.data.mean() * 1e9).round() / 1e9).item() <= 1.0,
                            msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                        )
                    else:
                        self.assertIn(
                            ((param.data.mean() * 1e9).round() / 1e9).item(),
                            [0.0, 1.0],
                            msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                        )

    @unittest.skip(reason="Feed forward chunking is not implemented")
    def test_feed_forward_chunking(self):
        pass

    @slow
    def test_model_from_pretrained(self):
        model = HubertModel.from_pretrained("facebook/hubert-base-ls960", from_pt=True)
        self.assertIsNotNone(model)


@require_mindspore
class HubertRobustModelTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (HubertForCTC, HubertForSequenceClassification, HubertModel) if is_mindspore_available() else ()
    test_pruning = False
    test_headmasking = False

    def setUp(self):
        self.model_tester = HubertModelTester(
            self, conv_stride=(3, 3, 3), feat_extract_norm="layer", do_stable_layer_norm=True
        )
        self.config_tester = ConfigTester(self, config_class=HubertConfig, hidden_size=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    def test_batched_inference(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_batch_inference(*config_and_inputs)

    def test_ctc_loss_inference(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_ctc_loss(*config_and_inputs)

    def test_seq_classifier_loss_inference(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_seq_classifier_loss(*config_and_inputs)

    @unittest.skip('ignore train temporarily')
    def test_ctc_train(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_ctc_training(*config_and_inputs)

    @unittest.skip('ignore train temporarily')
    def test_seq_classifier_train(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_seq_classifier_training(*config_and_inputs)

    def test_labels_out_of_vocab(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_labels_out_of_vocab(*config_and_inputs)

    # Hubert has no inputs_embeds
    def test_inputs_embeds(self):
        pass

    # `input_ids` is renamed to `input_values`
    def test_forward_signature(self):
        pass

    # Hubert cannot resize token embeddings
    # since it has no tokens embeddings
    def test_resize_tokens_embeddings(self):
        pass

    # Hubert has no inputs_embeds
    # and thus the `get_input_embeddings` fn
    # is not implemented
    def test_model_common_attributes(self):
        pass

    def test_initialization(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        configs_no_init = _config_zero_init(config)
        for model_class in self.all_model_classes:
            model = model_class(config=configs_no_init)
            for name, param in model.parameters_and_names():
                uniform_init_parms = [
                    "conv.weight",
                    "conv.parametrizations.weight",
                    "masked_spec_embed",
                    "quantizer.weight_proj.weight",
                ]
                if param.requires_grad:
                    if any(x in name for x in uniform_init_parms):
                        self.assertTrue(
                            -1.0 <= ((param.data.mean() * 1e9).round() / 1e9).item() <= 1.0,
                            msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                        )
                    else:
                        self.assertIn(
                            ((param.data.mean() * 1e9).round() / 1e9).item(),
                            [0.0, 1.0],
                            msg=f"Parameter {name} of model {model_class} seems not properly initialized",
                        )

    @unittest.skip(reason="Feed forward chunking is not implemented")
    def test_feed_forward_chunking(self):
        pass

    @slow
    def test_model_from_pretrained(self):
        model = HubertModel.from_pretrained("facebook/hubert-large-ls960-ft", from_pt=True)
        self.assertIsNotNone(model)


@require_mindspore
class HubertUtilsTest(unittest.TestCase):
    def test_compute_mask_indices(self):
        batch_size = 4
        sequence_length = 60
        mask_prob = 0.5
        mask_length = 1

        mask = _compute_mask_indices((batch_size, sequence_length), mask_prob, mask_length)
        mask = Tensor.from_numpy(mask)

        self.assertListEqual(mask.sum(axis=-1).tolist(), [mask_prob * sequence_length for _ in range(batch_size)])

    def test_compute_mask_indices_overlap(self):
        batch_size = 4
        sequence_length = 80
        mask_prob = 0.5
        mask_length = 4

        mask = _compute_mask_indices((batch_size, sequence_length), mask_prob, mask_length)
        mask = Tensor.from_numpy(mask)

        # because of overlap mask don't have to add up exactly to `mask_prob * sequence_length`, but have to be smaller or equal
        for batch_sum in mask.sum(axis=-1):
            self.assertTrue(int(batch_sum) <= mask_prob * sequence_length)


@require_mindspore
@slow
class HubertModelIntegrationTest(unittest.TestCase):
    def _load_datasamples(self, num_samples):
        ds = load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation", trust_remote_code=True)
        # automatic decoding with librispeech
        speech_samples = ds.sort("id").filter(
            lambda x: x["id"] in [f"1272-141231-000{i}" for i in range(num_samples)]
        )[:num_samples]["audio"]
        return [x["array"] for x in speech_samples]

    def _load_superb(self, task, num_samples):
        ds = load_dataset("anton-l/superb_dummy", task, split="test", trust_remote_code=True)
        return ds[:num_samples]

    def test_inference_ctc_batched(self):
        model = HubertForCTC.from_pretrained("facebook/hubert-large-ls960-ft", from_pt=True).half()
        processor = Wav2Vec2Processor.from_pretrained("facebook/hubert-large-ls960-ft", from_pt=True, do_lower_case=True)

        input_speech = self._load_datasamples(2)
        inputs = processor(input_speech, return_tensors="ms", padding=True)

        input_values = inputs.input_values.half()
        attention_mask = inputs.attention_mask

        logits = model(input_values, attention_mask=attention_mask).logits

        predicted_ids = F.argmax(logits, dim=-1)
        predicted_trans = processor.batch_decode(predicted_ids)

        EXPECTED_TRANSCRIPTIONS = [
            "a man said to the universe sir i exist",
            "sweat covered brion's body trickling into the tight loin cloth that was the only garment he wore",
        ]
        self.assertListEqual(predicted_trans, EXPECTED_TRANSCRIPTIONS)

    def test_inference_keyword_spotting(self):
        # NOTE: 原仓库代码用 float16 的精度也过不了测试 :(
        model = HubertForSequenceClassification.from_pretrained("superb/hubert-base-superb-ks", from_pt=True)#.half()
        processor = Wav2Vec2FeatureExtractor.from_pretrained("superb/hubert-base-superb-ks", from_pt=True)
        input_data = self._load_superb("ks", 4)
        inputs = processor(input_data["speech"], return_tensors="ms", padding=True)

        input_values = inputs.input_values#.half()
        attention_mask = inputs.attention_mask
        outputs = model(input_values, attention_mask=attention_mask)
        predicted_logits, predicted_ids = F.max(outputs.logits, axis=-1)

        expected_labels = [2, 6, 10, 9]
        # s3prl logits for the same batch
        expected_logits = Tensor([7.6692, 17.7795, 11.1562, 11.8232], dtype=ms.float32) # ms.float16

        self.assertListEqual(predicted_ids.tolist(), expected_labels)
        self.assertTrue(mnp.allclose(predicted_logits, expected_logits, atol=3e-2))

    def test_inference_intent_classification(self):
        model = HubertForSequenceClassification.from_pretrained("superb/hubert-base-superb-ic", from_pt=True).half()
        processor = Wav2Vec2FeatureExtractor.from_pretrained("superb/hubert-base-superb-ic", from_pt=True)
        input_data = self._load_superb("ic", 4)
        inputs = processor(input_data["speech"], return_tensors="ms", padding=True)

        input_values = inputs.input_values.half()
        attention_mask = inputs.attention_mask
        outputs = model(input_values, attention_mask=attention_mask)

        predicted_logits_action, predicted_ids_action = F.max(outputs.logits[:, :6], axis=-1)
        predicted_logits_object, predicted_ids_object = F.max(outputs.logits[:, 6:20], axis=-1)
        predicted_logits_location, predicted_ids_location = F.max(outputs.logits[:, 20:24], axis=-1)

        expected_labels_action = [1, 0, 4, 3]
        expected_logits_action = Tensor([5.9052, 12.5865, 4.4840, 10.0240], dtype=ms.float16)
        expected_labels_object = [1, 10, 3, 4]
        expected_logits_object = Tensor([5.5316, 11.7946, 8.1672, 23.2415], dtype=ms.float16)
        expected_labels_location = [0, 0, 0, 1]
        expected_logits_location = Tensor([5.2053, 8.9577, 10.0447, 8.1481], dtype=ms.float16)

        self.assertListEqual(predicted_ids_action.tolist(), expected_labels_action)
        self.assertListEqual(predicted_ids_object.tolist(), expected_labels_object)
        self.assertListEqual(predicted_ids_location.tolist(), expected_labels_location)

        # TODO: lower the tolerance after merging the padding fix https://github.com/pytorch/fairseq/pull/3572
        self.assertTrue(mnp.allclose(predicted_logits_action, expected_logits_action, atol=3e-1))
        self.assertTrue(mnp.allclose(predicted_logits_object, expected_logits_object, atol=3e-1))
        self.assertTrue(mnp.allclose(predicted_logits_location, expected_logits_location, atol=3e-1))

    def test_inference_speaker_identification(self):
        # NOTE: 原仓库代码用 float16 的精度也过不了测试 :(
        model = HubertForSequenceClassification.from_pretrained("superb/hubert-base-superb-sid", from_pt=True)#.half()
        processor = Wav2Vec2FeatureExtractor.from_pretrained("superb/hubert-base-superb-sid", from_pt=True)
        input_data = self._load_superb("si", 4)

        output_logits = []
        for example in input_data["speech"]:
            input = processor(example, return_tensors="ms", padding=True)
            output = model(input.input_values, attention_mask=None)  #.half()
            output_logits.append(output.logits[0])
        output_logits = F.stack(output_logits)
        predicted_logits, predicted_ids = F.max(output_logits, axis=-1)

        expected_labels = [5, 1, 1, 3]
        # s3prl logits for the same batch
        expected_logits = Tensor(
            [78231.5547, 123166.6094, 122785.4141, 84851.2969], dtype=ms.float32    # ms.float16
        )

        self.assertListEqual(predicted_ids.tolist(), expected_labels)
        # TODO: lower the tolerance after merging the padding fix https://github.com/pytorch/fairseq/pull/3572
        self.assertTrue(mnp.allclose(predicted_logits, expected_logits, atol=10))

    def test_inference_emotion_recognition(self):
        model = HubertForSequenceClassification.from_pretrained("superb/hubert-base-superb-er", from_pt=True).half()
        processor = Wav2Vec2FeatureExtractor.from_pretrained("superb/hubert-base-superb-er", from_pt=True)
        input_data = self._load_superb("er", 4)
        inputs = processor(input_data["speech"], return_tensors="ms", padding=True)

        input_values = inputs.input_values.half()
        attention_mask = inputs.attention_mask
        outputs = model(input_values, attention_mask=attention_mask)
        predicted_logits, predicted_ids = F.max(outputs.logits, axis=-1)

        expected_labels = [1, 1, 2, 2]
        # s3prl logits for the same batch
        expected_logits = Tensor([2.8384, 2.3389, 3.8564, 4.5558], dtype=ms.float16)

        self.assertListEqual(predicted_ids.tolist(), expected_labels)
        # TODO: lower the tolerance after merging the padding fix https://github.com/pytorch/fairseq/pull/3572
        self.assertTrue(mnp.allclose(predicted_logits, expected_logits, atol=1e-1))

    def test_inference_distilhubert(self):
        model = HubertModel.from_pretrained("ntu-spml/distilhubert", from_pt=True).half()
        processor = Wav2Vec2FeatureExtractor.from_pretrained("ntu-spml/distilhubert", from_pt=True)

        # TODO: can't test on batched inputs due to incompatible padding https://github.com/pytorch/fairseq/pull/3572
        input_speech = self._load_datasamples(1)
        inputs = processor(input_speech, return_tensors="ms", padding=True)
        input_values = inputs.input_values.half()
        outputs = model(input_values).last_hidden_state

        # expected outputs taken from the original SEW implementation
        expected_outputs_first = Tensor(
            [
                [
                    [-0.3505, 0.1167, 0.0608, 0.1294],
                    [-0.3085, 0.0481, 0.1106, 0.0955],
                    [-0.3107, -0.0391, 0.0739, 0.1360],
                    [-0.2385, -0.1795, -0.0928, 0.2389],
                ]
            ],
        )
        expected_outputs_last = Tensor(
            [
                [
                    [-0.0732, 0.0255, 0.0529, -0.1372],
                    [-0.0812, 0.1259, 0.0564, -0.0438],
                    [-0.0054, 0.0758, -0.0002, -0.1617],
                    [0.0133, -0.0320, -0.0687, 0.0062],
                ]
            ],
        )
        expected_output_sum = -3776.0730

        self.assertTrue(mnp.allclose(outputs[:, :4, :4], expected_outputs_first, atol=5e-3))
        self.assertTrue(mnp.allclose(outputs[:, -4:, -4:], expected_outputs_last, atol=5e-3))
        self.assertTrue(abs(outputs.sum() - expected_output_sum) < 0.1)
