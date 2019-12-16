import tensorflow as tf
import os
import scene_parsing_data.utils
import gscnn.loss as gscnn_loss
from time import time

class Trainer:
    def __init__(self, model,  train_dataset, val_dataset, epochs, optimiser, log_dir, model_dir, l1, l2, l3, l4):
        self.weights = [l1, l2, l3, l4]
        self.model = model
        self.train_dataset = train_dataset
        self.val_dataset = val_dataset
        self.epochs = epochs
        self.optimiser = optimiser
        self.train_step_counter = tf.Variable(0, name='step_train', dtype=tf.int64)
        self.val_step_counter = tf.Variable(0, name='step_val', dtype=tf.int64)

        train_log_dir = os.path.join(log_dir, 'train')
        val_log_dir = os.path.join(log_dir, 'val')
        self.train_writer = tf.summary.create_file_writer(train_log_dir)
        self.val_writer = tf.summary.create_file_writer(val_log_dir)
        self.log_freq = 100
        self.model_dir = model_dir

        # will build summaries in forward pass
        self.recorded_tensors = {
            'image': None,
            'label': None,
            'edge_label': None,
            'pred_label': None,
            'pred_shape': None,
            'seg_loss': None,
            'edge_loss': None,
            'edge_consistency': None,
            'edge_class_consistency': None,
            'loss': None,
            'accuracy': None,
        }
        self.epoch_metrics = {
            'accuracy': tf.keras.metrics.Mean(),
            'loss': tf.keras.metrics.Mean()}

        self.best_accuracy = -1.

    def log_pass(self, im, label, edge_label, logits, shape_head, sub_losses, train):
        step = self.train_step_counter if train else self.val_step_counter
        if step.numpy()%self.log_freq != 0:
            return
        # convert to colour palette
        label_flat = tf.argmax(label, axis=-1)
        pred_label_flat = tf.argmax(tf.nn.softmax(logits), axis=-1)
        colour_array = tf.constant(scene_parsing_data.COLOURS)
        label_image = tf.gather(colour_array, label_flat)
        pred_label_image = tf.gather(colour_array, pred_label_flat)

        seg_loss, edge_loss, edge_class_consistency, edge_consistency = sub_losses
        loss = sum(sub_losses)

        correct = tf.reduce_sum(tf.cast(label_flat == pred_label_flat, tf.float32))
        total_vals = tf.shape(tf.reshape(pred_label_flat, [-1]))[0]
        accuracy = correct/tf.cast(total_vals, tf.float32)

        self.epoch_metrics['accuracy'].update_state(accuracy)
        self.epoch_metrics['loss'].update_state(loss)

        with tf.summary.record_if(tf.equal(tf.math.mod(step, self.log_freq), 0)):
            with tf.summary.record_if(tf.equal(tf.math.mod(step, self.log_freq*3), 0)):
                tf.summary.image(
                    'edge_comparison',
                    tf.concat([edge_label[..., 1:], shape_head], axis=1),
                    step=step)
                tf.summary.image(
                    'label_comparison',
                    tf.concat([tf.cast(im, tf.uint8), label_image, pred_label_image], axis=1),
                    step=step)
            tf.summary.scalar('seg_loss', seg_loss, step=step)
            tf.summary.scalar('edge_loss', edge_loss, step=step)
            tf.summary.scalar('edge_class_consistency', edge_class_consistency, step=step)
            tf.summary.scalar('edge_consistency', edge_consistency, step=step)
            tf.summary.scalar('batch_loss', loss, step=step)
            tf.summary.scalar('batch_accuracy', accuracy, step=step)

    @tf.function
    def forward_pass(self, im, label, edge_label, train):
        prediction, shape_head = self.model(im, training=train)
        sub_losses = gscnn_loss.loss(label, prediction, shape_head, edge_label, self.weights)
        return prediction, shape_head, sub_losses

    @tf.function
    def train_step(self, im, label, edge_label):
        with tf.GradientTape() as tape:
            prediction, shape_head, sub_losses = self.forward_pass(im, label, edge_label, train=True)
            loss = sum(sub_losses)
        gradients = tape.gradient(loss, self.model.trainable_variables)
        self.optimiser.apply_gradients(zip(gradients, self.model.trainable_variables))
        return prediction, shape_head, sub_losses

    def get_summary_writer(self, train):
        return self.train_writer if train else self. val_writer

    def log_metrics(self, train, epoch):
        writer = self.train_writer if train else self. val_writer
        with writer.as_default():
            for k in self.epoch_metrics:
                tf.summary.scalar('epoch_' + k, self.epoch_metrics[k].result(), step=epoch)
                self.epoch_metrics[k].reset_states()

    def train_epoch(self, ):
        with self.train_writer.as_default():
            for im, label, edge_label in self.train_dataset:
                prediction, shape_head, sub_losses = self.train_step(im, label, edge_label)
                self.log_pass(im, label, edge_label, prediction, shape_head, sub_losses, train=True)
                self.train_step_counter.assign_add(1)

    def val_epoch(self,):
        with self.val_writer.as_default():
            for im, label, edge_label in self.val_dataset:
                prediction, shape_head, sub_losses = self.forward_pass(im, label, edge_label, train=False)
                self.log_pass(im, label, edge_label, prediction, shape_head, sub_losses, train=False)
                self.val_step_counter.assign_add(1)

    def make_weight_path(self, epoch):
        return os.path.join(
            self.model_dir,
            'epoch_{}_val_acc_{}'.format(epoch, self.epoch_metrics['accuracy'].result()))

    def train_loop(self):
        for epoch in range(self.epochs):
            st = time()
            print('Epoch {}'.format(epoch))
            print('Training')
            self.train_epoch()
            print('Training an epoch took {}'.format(time() - st))
            self.log_metrics(train=True, epoch=epoch)
            print('Validating')
            st = time()
            self.val_epoch()
            print('Validating an epoch took {}'.format(time() - st))
            if self.epoch_metrics['accuracy'].result() > self.best_accuracy:
                self.model.save_weights(
                    self.make_weight_path(epoch),
                    save_format='tf')
                self.best_accuracy = self.epoch_metrics['accuracy'].result()
            self.log_metrics(train=False, epoch=epoch)



