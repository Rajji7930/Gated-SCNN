import tensorflow as tf


def generalised_dice(y_true, y_pred, eps=0.0):
    # [b, h, w, classes]
    y_pred = tf.nn.softmax(y_pred)
    y_true_shape = tf.shape(y_true)

    # [b, h*w, classes]
    y_true = tf.reshape(y_true, [-1, y_true_shape[1] * y_true_shape[2], y_true_shape[3]])
    y_pred = tf.reshape(y_pred, [-1, y_true_shape[1] * y_true_shape[2], y_true_shape[3]])

    # [b, classes]
    counts = tf.reduce_sum(y_true, axis=1)
    weights = 1. / counts**2
    weights = tf.where(tf.math.is_finite(weights), weights, eps)

    multed = tf.reduce_sum(y_true * y_pred, axis=1)
    summed = tf.reduce_sum(y_true + y_pred, axis=1)

    # [b]
    numerators = tf.reduce_sum(weights * multed, axis=-1)
    denom = tf.reduce_sum(weights * summed, axis=-1)
    dices = 1. - 2. * numerators / denom
    dices = tf.where(tf.math.is_finite(dices), dices, tf.zeros_like(dices))
    return tf.reduce_mean(dices)


def _edge_mag(tensor, eps=1e-8):
    tensor_edge = tf.image.sobel_edges(tensor)
    mag = tf.reduce_sum(tensor_edge ** 2, axis=-1) + eps
    mag = tf.math.sqrt(mag)
    mag /= tf.reduce_max(mag, axis=[1, 2], keepdims=True)
    return mag


def _gumbel_softmax(logits, eps=1e-8, tau=1.):
    g = tf.random.uniform(tf.shape(logits))
    g = -tf.math.log(eps - tf.math.log(g + eps))
    return tf.nn.softmax((logits + g) / tau)


def segmentation_edge_loss(gt_tensor, pred_tensor, thresh=0.8):
    pred_tensor = _gumbel_softmax(pred_tensor)
    gt_edges = _edge_mag(gt_tensor)
    pred_edges = _edge_mag(pred_tensor)

    gt_edges = tf.reshape(gt_edges, [-1, tf.shape(gt_edges)[-1]])
    pred_edges = tf.reshape(pred_edges, [-1, tf.shape(gt_edges)[-1]])

    edge_difference = tf.abs(gt_edges - pred_edges)

    mask_gt = tf.cast((gt_edges > thresh ** 2), tf.float32)
    contrib_0 = tf.reduce_mean(tf.boolean_mask(edge_difference, mask_gt))
    mask_pred = tf.stop_gradient(tf.cast((pred_edges > thresh ** 2), tf.float32))
    contrib_1 = tf.reduce_mean(tf.boolean_mask(edge_difference, mask_pred))

    return tf.reduce_mean(0.5 * contrib_0 + 0.5 * contrib_1)


def shape_edge_loss(gt_tensor, pred_tensor, pred_shape_tensor, keep_mask, thresh=0.8):
    mask = pred_shape_tensor > thresh
    mask = tf.stop_gradient(mask[..., 0])
    mask = tf.logical_and(mask, keep_mask)
    gt = gt_tensor[mask]
    pred = pred_tensor[mask]

    if tf.reduce_sum(tf.cast(mask, tf.float32)) > 0:
        return tf.reduce_mean(tf.losses.categorical_crossentropy(gt, pred, from_logits=True))
    else:
        return 0.


def weighted_cross_entropy(y_true, y_pred):
    # weights
    rs = tf.reduce_sum(y_true, axis=0, keepdims=True)
    N = tf.cast(tf.shape(y_true)[0], tf.float32)
    weights = (N - rs)/N + 1

    # everything here is one hot so this essentially picks the class weight
    # per row of y_true
    weights = tf.reduce_sum(y_true*weights, axis=1)

    # compute your (unweighted) softmax cross entropy loss
    unweighted_losses = tf.nn.softmax_cross_entropy_with_logits(y_true, y_pred)
    weighted_losses = unweighted_losses * weights
    loss = tf.reduce_mean(weighted_losses)
    return loss


def loss(gt_label, logits, shape_head, edge_label, loss_weights):

    keep_mask = tf.reduce_any(gt_label == 1., axis=-1)
    seg_loss = weighted_cross_entropy(gt_label[keep_mask], logits[keep_mask])*loss_weights[0]

    # dice loss for edges
    shape_probs = tf.concat([1. - shape_head, shape_head], axis=-1)
    edge_loss = generalised_dice(edge_label, shape_probs) * loss_weights[1]

    # regularizing loss
    edge_consistency = segmentation_edge_loss(gt_label, logits) * loss_weights[2]
    edge_class_consistency = shape_edge_loss(gt_label, logits, shape_head, keep_mask) * loss_weights[3]
    return seg_loss, edge_loss, edge_class_consistency, edge_consistency

