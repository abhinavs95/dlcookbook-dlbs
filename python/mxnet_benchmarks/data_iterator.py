# (c) Copyright [2017] Hewlett Packard Enterprise Development LP
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Classes defined in this module implement various data iterators.

Data iterators are created after a model has been created, so, shape and layout of input data tensors are known and
cannot be changed.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import os
import mxnet as mx
from mxnet.io import DataBatch, DataIter
import numpy as np


class SyntheticDataIterator(DataIter):
    """ Feeds synthetic (random) data.
    
    See this page for more details:
    https://github.com/apache/incubator-mxnet/blob/master/example/image-classification/common/data.py
    Works with two standard input tensors - data tensor and label tensor.
    """
    def __init__(self, data_shape, label_shape, labels_range, opts):
        """MXNet partitions data batch evenly among the available GPUs. Here, the
           batch size is the effective batch size.
           
        Memory for data and label tensors is allocated with `cpu_pinned` context.
        To make this iterator consistent with other iterators that provide real data,
        I always set maximal number of iterations to be `warmup_iters + bench_iters`.

        Changes:
            New version does not support `max_iter` parameter. To limit number of batches, wrap this iterator with
                `mx.io.ResizeIter`.

        Args:
            data_shape (tuple): Shape of input data tensor (X) including batch size. The batch size is the 0th
                dimension (bsz = data_shape[0]). This batch size must be an effective batch for a whole node.
            label_shape (tuple): Shape of input label tensor (Y) including batch size. The batch size is the 0th
                dimension (bsz = labels_shape[0]). This batch size must be an effective batch for a whole node.
            labels_range (list): List of output labels. For ImageNet, that would be a list with integers from 0 to 999.
            dtype (str): Data type for data tensor (float32, float16).
        """
        super(SyntheticDataIterator, self).__init__(data_shape[0])
        print("Creating synthetic data iterator with data shape {} "
              "and data layout {}.".format(data_shape, opts['input_layout']))
        # Let's assume this data iterator always returns single precision tensors.
        self.dtype = opts['dtype']
        # self.dtype = 'float32'
        # mx.Context: cpu, gpu, cpu_pinned, cpu_shared
        self.data = mx.nd.array(np.random.uniform(-1, 1, data_shape),
                                dtype=self.dtype,
                                ctx=mx.Context('cpu_pinned', 0))
        self.label_shape = [label_shape[0]]
        if not self.label_shape:
            self.label_shape = [self.batch_size]
        self.label = mx.nd.array(np.random.randint(labels_range[0], labels_range[1] + 1, self.label_shape),
                                 dtype='float32',
                                 ctx=mx.Context('cpu_pinned', 0))

    def __iter__(self):
        return self

    @property
    def provide_data(self):
        return [mx.io.DataDesc('data', self.data.shape, self.dtype)]

    @property
    def provide_label(self):
        return [mx.io.DataDesc('softmax_label', self.label_shape, 'float32')]

    def next(self):
        """For DataBatch definition, see this page:
           https://mxnet.incubator.apache.org/api/python/io.html#mxnet.io.DataBatch
        """
        return DataBatch(data=(self.data,), label=(self.label,), pad=0, index=None, provide_data=self.provide_data,
                         provide_label=self.provide_label)

    def __next__(self):
        return self.next()


class DataIteratorFactory(object):
    """A factory that now creates two types of data iterators.
    
    The one is a synthetic data iterator that feeds random tensors, the other one
    is actually an ImageRecordIter.
    """
    @staticmethod
    def get(data_shape, label_shape, labels_range, opts, kv_store=None):
        """Creates data iterator.

        Args:
            data_shape (tuple): Shape of input data tensor (X) including batch size. The batch size is the 0th
                dimension (bsz = data_shape[0]). This batch size must be an effective batch for a whole node.
            label_shape (tuple): Shape of input label tensor (Y) including batch size. The batch size is the 0th
                dimension (bsz = labels_shape[0]). This batch size must be an effective batch for a whole node.
            labels_range (list): List of output labels. For ImageNet, that would be a list with integers from 0 to 999.
            opts (dict): Dictionary with options.
            kv_store (mxnet.kvstore.KVStore): An object returned by mx.kvstore.create('...').

        Returns:
            Data iterator (instance of mx.io.DataIter).
        """
        if 'data_dir' not in opts or not opts['data_dir']:
            data_iter = SyntheticDataIterator(data_shape, label_shape, labels_range, opts)
        else:
            (rank, nworker) = (kv_store.rank, kv_store.num_workers) if kv_store else (0, 1)
            # https://mxnet.incubator.apache.org/api/python/io.html#mxnet.io.ImageRecordIter
            # https://github.com/apache/incubator-mxnet/blob/master/example/image-classification/common/data.py
            # This iterator supports channels first format only.
            input_layout = opts.get('input_layout', 'NCHW')
            if input_layout != 'NCHW':
                raise ValueError("Standard MXNET image record iterator only supports channel first format (NCHW), "
                                 "requested format: {}.".format(input_layout))
            dataset_files = [
                os.path.join(opts['data_dir'], 'train.rec'),
                os.path.join(opts['data_dir'], 'train.idx')
            ]
            for dataset_file in dataset_files:
                if not os.path.exists(dataset_file):
                    raise ValueError("Missing mandatory dataset file '{}'. The train directory '{}' must "
                                     "contain at least two files - train.rec and train.idx.".format(dataset_file,
                                                                                                    opts['data_dir']))
            print("Creating standard image record iterator (ImageRecordIter) with data layout {}.".format(input_layout))
            data_iter = mx.io.ImageRecordIter(
                path_imgrec=dataset_files[0],
                path_imgidx=dataset_files[1],
                data_name='data',
                label_name='softmax_label',
                data_shape=(data_shape[1], data_shape[2], data_shape[3]),
                batch_size=data_shape[0],
                rand_crop=True,
                rand_mirror=True,
                preprocess_threads=opts.get('preprocess_threads', 4),
                prefetch_buffer=opts.get('prefetch_buffer ', 10),
                dtype='float32',
                num_parts=nworker,
                part_index=rank
            )
            # dtype=opts['dtype']
        return data_iter
