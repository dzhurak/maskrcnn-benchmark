# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
import datetime
import logging
import time

import torch
import torch.distributed as dist

from maskrcnn_benchmark.utils.comm import get_world_size
from maskrcnn_benchmark.utils.metric_logger import MetricLogger


def reduce_loss_dict(loss_dict):
    """
    Reduce the loss dictionary from all processes so that process with rank
    0 has the averaged results. Returns a dict with the same fields as
    loss_dict, after reduction.
    """
    world_size = get_world_size()
    if world_size < 2:
        return loss_dict
    with torch.no_grad():
        loss_names = []
        all_losses = []
        for k in sorted(loss_dict.keys()):
            loss_names.append(k)
            all_losses.append(loss_dict[k])
        all_losses = torch.stack(all_losses, dim=0)
        dist.reduce(all_losses, dst=0)
        if dist.get_rank() == 0:
            # only main process gets accumulated, so only divide by
            # world_size in this case
            all_losses /= world_size
        reduced_losses = {k: v for k, v in zip(loss_names, all_losses)}
    return reduced_losses


def do_train(
    model,
    train_data_loader,
    test_data_loader,
    optimizer,
    scheduler,
    device,
    arguments,
):
    logging.debug("Start training")
    train_meters = MetricLogger(delimiter="  ")
    max_iter = len(train_data_loader)
    start_iter = arguments["iteration"]
    model.train()
    start_training_time = time.time()

    for iteration, (images, targets, _) in enumerate(train_data_loader, start_iter):
        iteration = iteration + 1
        arguments["iteration"] = iteration

        scheduler.step()
        images = images.to(device)
        targets = [target.to(device) for target in targets]

        try:
            loss_dict = model(images, targets)
        except:
            continue

        losses = sum(loss for loss in loss_dict.values())

        # reduce losses over all GPUs for logging purposes
        loss_dict_reduced = reduce_loss_dict(loss_dict)
        losses_reduced = sum(loss for loss in loss_dict_reduced.values())
        train_meters.update(loss=losses_reduced, **loss_dict_reduced)

        optimizer.zero_grad()
        losses.backward()
        optimizer.step()

        if iteration % 10 == 0 or iteration == max_iter:
            logging.debug(train_meters)

    test_meters = MetricLogger(delimiter="  ")
    for (images, targets, _) in test_data_loader:
        images = images.to(device)
        targets = [target.to(device) for target in targets]

        try:
            test_loss_dict = model(images, targets)
        except:
            continue

        test_loss_dict_reduced = reduce_loss_dict(test_loss_dict)
        test_losses_reduced = sum(loss for loss in test_loss_dict_reduced.values())
        test_meters.update(loss=test_losses_reduced, **test_loss_dict_reduced)
    logging.debug("Test: %s", test_meters)

    total_training_time = time.time() - start_training_time
    total_time_str = str(datetime.timedelta(seconds=total_training_time))
    logging.debug(
        "Total training time: {} ({:.4f} s / it)".format(
            total_time_str, total_training_time / (max_iter)
        )
    )
    return train_meters, test_meters
