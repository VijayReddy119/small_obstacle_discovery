import argparse
import torch
from torch.utils.data import DataLoader
import os
import numpy as np
from tqdm import tqdm
from mypath import Path
from dataloaders import make_data_loader
from modeling.sync_batchnorm.replicate import patch_replication_callback
from modeling.deeplab import *
from utils.loss import SegmentationLosses
from utils.calculate_weights import calculate_weights_batch
from utils.lr_scheduler import LR_Scheduler
from utils.saver import Saver
from utils.summaries import TensorboardSummary
from utils.metrics import Evaluator
import utils.helpers as HLP

class Trainer(object):
        def __init__(self, args):
                self.args = args

                # Define Saver
                self.saver = Saver(args)
                self.saver.save_experiment_config()
                self.nclass = 10
                # Define Tensorboard Summary
                self.summary = TensorboardSummary(self.saver.experiment_dir)
                self.writer = self.summary.create_summary()

                # Define Dataloader
                kwargs = {'num_workers': args.workers, 'pin_memory': True}
                print('depth:',args.depth)
                # self.train_loader, self.val_loader, self.test_loader, self.nclass = make_data_loader(args, **kwargs) 
                if args.depth:
                    train_imgs, train_disp, train_labels = HLP.get_ImagesAndLabels_mergenet(Path.db_root_dir(args.dataset),
                                                       num_samples=args.num_samples)
                    test_imgs, test_disp, test_labels = HLP.get_ImagesAndLabels_mergenet(Path.db_root_dir(args.dataset),
                                                      data_type='test',
                                                      num_samples=args.num_samples)
                    self.train_loader = DataLoader(HLP.LNFGeneratorTorch(rgb_path=train_imgs,disparity_path=train_disp, mask_path=train_labels, flag = 'merge', split='train'), batch_size = self.args.batch_size, shuffle=True)
                    self.val_loader = DataLoader(HLP.LNFGeneratorTorch(rgb_path=test_imgs[:100],disparity_path=test_disp[:100], mask_path=test_labels[:100], flag = 'merge', split='val'), batch_size=self.args.batch_size, shuffle=True)

                    self.test_loader = DataLoader(HLP.LNFGeneratorTorch(rgb_path=test_imgs[100:],disparity_path=test_disp[100:],
                                          mask_path=test_labels[100:], flag = 'merge', split='test'), batch_size=self.args.batch_size)
                    # Define network
                    model = DeepLab(num_classes=self.nclass,
                                                    backbone=args.backbone,
                                                    output_stride=args.out_stride,
                                                    sync_bn=args.sync_bn,
                                                    freeze_bn=args.freeze_bn,
                                    depth=args.depth)
                else:
                    train_imgs, train_labels = HLP.get_ImagesAndLabels_contextnet(Path.db_root_dir(args.dataset),
                                                       num_samples=args.num_samples)
                    test_imgs, test_labels = HLP.get_ImagesAndLabels_contextnet(Path.db_root_dir(args.dataset),
                                                      data_type='test',
                                                      num_samples=args.num_samples)
                    self.train_loader = DataLoader(HLP.LNFGeneratorTorch(rgb_path=train_imgs,
                                                              mask_path=train_labels,
                                                             flag = 'context',
                                                                         split='train'),
                                                   batch_size =
                                                   self.args.batch_size,
                                                   shuffle=True)
                    self.val_loader = DataLoader(HLP.LNFGeneratorTorch(rgb_path=test_imgs[:100],
                                          mask_path=test_labels[:100], flag =
                                                            'context',
                                                                       split='val'),
                                                 batch_size=self.args.batch_size,
                                                 shuffle=True)

                    self.test_loader = DataLoader(HLP.LNFGeneratorTorch(rgb_path=test_imgs[100:],
                                          mask_path=test_labels[100:], flag =
                                                                        'context', split='test'), batch_size=self.args.batch_size)
                    # Define network
                    model = DeepLab(num_classes=self.nclass,
                                                    backbone=args.backbone,
                                                    output_stride=args.out_stride,
                                                    sync_bn=args.sync_bn,
                                                    freeze_bn=args.freeze_bn,
                                    depth=args.depth)

                train_params = [{'params': model.get_1x_lr_params(), 'lr': args.lr},
                                                {'params': model.get_10x_lr_params(), 'lr': args.lr * 10}]

                # Define Optimizer
                optimizer = torch.optim.SGD(train_params, momentum=args.momentum,
                                                                        weight_decay=args.weight_decay, nesterov=args.nesterov)

                # Define Criterion
                # whether to use class balanced weights
                """
                if args.use_balanced_weights:
                        classes_weights_path = os.path.join(Path.db_root_dir(args.dataset), args.dataset+'_classes_weights.npy')
                        if os.path.isfile(classes_weights_path):
                                weight = np.load(classes_weights_path)
                        else:
                                weight = calculate_weigths_labels(args.dataset, self.train_loader, self.nclass)
                        weight = torch.from_numpy(weight.astype(np.float32))
                else:
                        weight = None
                """
                self.criterion = SegmentationLosses(cuda=args.cuda)
                self.model, self.optimizer = model, optimizer

                # Define Evaluator
                self.evaluator = Evaluator(self.nclass)
                # Define lr scheduler
                self.scheduler = LR_Scheduler(args.lr_scheduler, args.lr,
                                                                                        args.epochs, len(self.train_loader))

                # write the graph
                tensor = torch.zeros([2, 3, 512, 512])
                self.writer.add_graph(model, tensor)

                # Using cuda
                if args.cuda:
                        self.model = torch.nn.DataParallel(self.model, device_ids=self.args.gpu_ids)
                        patch_replication_callback(self.model)
                        self.model = self.model.cuda()

                # Resuming checkpoint
                self.best_pred = 0.0
                if args.resume is not None:
                        if not os.path.isfile(args.resume):
                                raise RuntimeError("=> no checkpoint found at '{}'" .format(args.resume))

                        checkpoint = torch.load(args.resume)
                        args.start_epoch = checkpoint['epoch']
                        if args.cuda:
                                self.model.module.load_state_dict(checkpoint['state_dict'])
                        else:
                                self.model.load_state_dict(checkpoint['state_dict'])
                        if not args.ft:
                                self.optimizer.load_state_dict(checkpoint['optimizer'])
                        self.best_pred = checkpoint['best_pred']
                        print("=> loaded checkpoint '{}' (epoch {})".format(args.resume, checkpoint['epoch']))

                # Clear start epoch if fine-tuning
                if args.ft:
                        args.start_epoch = 0

        def training(self, epoch):
                train_loss = 0.0
                self.model.train()
                self.evaluator.reset()
                tbar = tqdm(self.train_loader, desc='training')
                num_img_tr = len(self.train_loader)
                recall=0.0                      # Just for small obstacle
                precision=0.0
                idr = 0
                for i, sample in enumerate(tbar):
                        image, target = sample['image'], sample['label']
                        if self.args.cuda:
                                image, target = image.cuda(), target.cuda()

                        self.scheduler(self.optimizer, i, epoch, self.best_pred)
                        self.optimizer.zero_grad()
                        output, conf, pre_conf = self.model(image)
                        loss = self.criterion.CrossEntropyLoss(output,target,weight=torch.from_numpy(calculate_weights_batch(sample,self.nclass).astype(np.float32)))
                        loss.backward()
                        self.optimizer.step()
                        train_loss += loss.item()
                        tbar.set_description('Train loss: %.3f' % (train_loss / (i + 1)))
                        self.writer.add_scalar('loss/train_batch_loss', loss.item(), i + num_img_tr * epoch)

                        # Show 10 * 3 inference results each epoch
                        if i % (num_img_tr // 10) == 0:
                                global_step = i + num_img_tr * epoch
                                if self.args.depth:
                                    self.summary.visualize_image(self.writer,
                                                                 self.args.dataset,
                                                                 image[:,:3,:,], target,
                                                                 output, conf,
                                                                 global_step,
                                                                 flag='train')
                                else:
                                    self.summary.visualize_image(self.writer,
                                                                 self.args.dataset,
                                                                 image, target,
                                                                 output, conf,
                                                                 global_step,
                                                                 flag='train')


                        pred = output.data.cpu().numpy()
                        target = target.cpu().numpy()
                        pred = np.argmax(pred, axis=1)
                        # Add batch sample into evaluator
                        self.evaluator.add_batch(target, pred)

                # Fast test during the training
                Acc = self.evaluator.Pixel_Accuracy()
                Acc_class = self.evaluator.Pixel_Accuracy_Class()
                mIoU = self.evaluator.Mean_Intersection_over_Union()
                FWIoU = self.evaluator.Frequency_Weighted_Intersection_over_Union()
                recall,precision=self.evaluator.pdr_metric(class_id=2)
                idr = self.evaluator.idr_metric(class_id=2)
                self.writer.add_scalar('loss/train_epoch_loss', train_loss, epoch)
                self.writer.add_scalar('metrics/train_miou', mIoU, epoch)
                self.writer.add_scalar('metrics/train_acc', Acc, epoch)
                self.writer.add_scalar('metrics/train_acc_cl', Acc_class, epoch)
                self.writer.add_scalar('metrics/train_fwIoU', FWIoU, epoch)
                if recall is not None:
                    self.writer.add_scalar('metrics/train_pdr_epoch',recall,epoch)
                if precision is not None:
                    self.writer.add_scalar('metrics/train_precision_epoch',precision,epoch)
                if idr is not None:
                    self.writer.add_scalar('metrics/train_idr_epoch', idr, epoch)

                if self.args.no_val:
                        # save checkpoint every epoch
                        is_best = False
                        self.saver.save_checkpoint({
                                'epoch': epoch + 1,
                                'state_dict': self.model.module.state_dict(),
                                'optimizer': self.optimizer.state_dict(),
                                'best_pred': self.best_pred,
                        }, is_best)


        def validation(self, epoch):

                if self.args.mode=="train" or self.args.mode=="val":
                        loader=self.val_loader
                        visualize_flag = 'val'
                elif self.args.mode=="test":
                        loader=self.test_loader
                        visualize_flag = 'test'

                self.model.eval()
                self.evaluator.reset()
                tbar = tqdm(loader, desc='validation')

                test_loss = 0.0
                recall=0.0                      # Just for small obstacle
                precision=0.0
                idr = 0
                num_itr=len(loader)

                for i, sample in enumerate(tbar):
                        image, target = sample['image'], sample['label']
                        if self.args.cuda:
                                image, target = image.cuda(), target.cuda()
                        with torch.no_grad():
                                x = self.model(image)
                                output, conf = x[0], x[1]
                                print(output.shape)
                                loss = self.criterion.CrossEntropyLoss(output,target,weight=torch.from_numpy(calculate_weights_batch(sample,self.nclass).astype(np.float32)))
                                test_loss += loss.item()
                        tbar.set_description('Test loss: %.3f' % (test_loss / (i + 1)))
                        if i % (num_itr // 5) == 0:
                                global_step = i + num_itr * epoch
                                if not self.args.depth:
                                    self.summary.visualize_image(self.writer,
                                                                 self.args.dataset,
                                                                 image, target,
                                                                 output, conf,
                                                                 global_step,
                                                                 flag=visualize_flag)
                                else:
                                    self.summary.visualize_image(self.writer,
                                                                 self.args.dataset,
                                                                 image[:,:3,:,:], target,
                                                                 output, conf,
                                                                 global_step,
                                                                 flag=visualize_flag)

                        pred = output.data.cpu().numpy()
                        target = target.cpu().numpy()
                        pred = np.argmax(pred, axis=1)
                        # Add batch sample into evaluator
                        self.evaluator.add_batch(target, pred)
                # Fast test during the training
                Acc = self.evaluator.Pixel_Accuracy()
                Acc_class = self.evaluator.Pixel_Accuracy_Class()
                mIoU = self.evaluator.Mean_Intersection_over_Union()
                FWIoU = self.evaluator.Frequency_Weighted_Intersection_over_Union()
                recall,precision=self.evaluator.pdr_metric(class_id=2)
                idr = self.evaluator.idr_metric(class_id=2)
                self.writer.add_scalar('loss/val_epoch_loss', test_loss, epoch)
                self.writer.add_scalar('metrics/val_miou', mIoU, epoch)
                self.writer.add_scalar('metrics/val_acc', Acc, epoch)
                self.writer.add_scalar('metrics/val_acc_cl', Acc_class, epoch)
                self.writer.add_scalar('metrics/val_fwIoU', FWIoU, epoch)
                self.writer.add_scalar('metrics/val_pdr_epoch',recall,epoch)
                self.writer.add_scalar('metrics/val_precision_epoch',precision,epoch)
                self.writer.add_scalar('metrics/val_idr_epoch', idr, epoch)

                print('Validation:')
                print('[Epoch: %d, numImages: %5d]' % (epoch, i * self.args.batch_size + image.data.shape[0]))
                print("Acc:{}, Acc_class:{}, mIoU:{}, fwIoU: {}".format(Acc, Acc_class, mIoU, FWIoU))
                print('Loss: %.3f' % test_loss)
                print('Recall/PDR:{}'.format(recall))
                print('Precision:{}'.format(precision))

                new_pred = mIoU
                if new_pred > self.best_pred:
                        is_best = True
                        self.best_pred = new_pred
                        self.saver.save_checkpoint({
                                'epoch': epoch + 1,
                                'state_dict': self.model.module.state_dict(),
                                'optimizer': self.optimizer.state_dict(),
                                'best_pred': self.best_pred,
                        }, is_best)

def main():
        parser = argparse.ArgumentParser(description="PyTorch DeeplabV3Plus Training")
        parser.add_argument('--backbone', type=str, default='drn',
                                                choices=['resnet', 'xception', 'drn', 'mobilenet'],
                                                help='backbone name (default: drn)')
        parser.add_argument('--out-stride', type=int, default=16,
                                                help='network output stride (default: 8)')
        parser.add_argument('--dataset', type=str, default='small_obstacle',
                                                choices=['pascal', 'coco', 'cityscapes', 'lnf'],
                                                help='dataset name (default: pascal)')
        parser.add_argument('--use-sbd', action='store_true', default=False,
                                                help='whether to use SBD dataset (default: True)')
        parser.add_argument('--workers', type=int, default=4,
                                                metavar='N', help='dataloader threads')
        parser.add_argument('--base-size', type=int, default=512,
                                                help='base image size')
        parser.add_argument('--crop-size', type=int, default=512,
                                                help='crop image size')
        parser.add_argument('--sync-bn', type=bool, default=None,
                                                help='whether to use sync bn (default: auto)')
        parser.add_argument('--freeze-bn', type=bool, default=False,
                                                help='whether to freeze bn parameters (default: False)')
        parser.add_argument('--loss-type', type=str, default='ce',
                                                choices=['ce', 'focal'],
                                                help='loss func type (default: ce)')
        # training hyper params
        parser.add_argument('--epochs', type=int, default=None, metavar='N',
                                                help='number of epochs to train (default: auto)')
        parser.add_argument('--start_epoch', type=int, default=0,
                                                metavar='N', help='start epochs (default:0)')
        parser.add_argument('--batch-size', type=int, default=8,
                                                metavar='N', help='input batch size for \
                                                                training (default: auto)')
        parser.add_argument('--test-batch-size', type=int, default=None,
                                                metavar='N', help='input batch size for \
                                                                testing (default: auto)')
        parser.add_argument('--use-balanced-weights', action='store_true', default=True,
                                                help='whether to use balanced weights (default: False)')
        # optimizer params
        parser.add_argument('--lr', type=float, default=None, metavar='LR',
                                                help='learning rate (default: auto)')
        parser.add_argument('--lr-scheduler', type=str, default='poly',
                                                choices=['poly', 'step', 'cos'],
                                                help='lr scheduler mode: (default: poly)')
        parser.add_argument('--momentum', type=float, default=0.9,
                                                metavar='M', help='momentum (default: 0.9)')
        parser.add_argument('--weight-decay', type=float, default=5e-4,
                                                metavar='M', help='w-decay (default: 5e-4)')
        parser.add_argument('--nesterov', action='store_true', default=False,
                                                help='whether use nesterov (default: False)')
        # cuda, seed and logging
        parser.add_argument('--no-cuda', action='store_true', default=
                                                False, help='disables CUDA training')
        parser.add_argument('--gpu-ids', type=str, default='0',
                                                help='use which gpu to train, must be a \
                                                comma-separated list of integers only (default=0,1)')
        parser.add_argument('--seed', type=int, default=1, metavar='S',
                                                help='random seed (default: 1)')
        # checking point
        parser.add_argument('--resume', type=str, default=None,
                                                help='put the path to resuming file if needed')
        parser.add_argument('--checkname', type=str, default=None,
                                                help='set the checkpoint name')
        # finetuning pre-trained models
        parser.add_argument('--ft', action='store_true', default=True,
                                                help='finetuning on a different dataset')
        # evaluation option
        parser.add_argument('--eval-interval', type=int, default=5,
                                                help='evaluuation interval (default: 1)')
        parser.add_argument('--no-val', action='store_true', default=False,
                                                help='skip validation during training')

        parser.add_argument('--mode',type=str,help='options=train/val/test')

        parser.add_argument('--num_samples', type=int, default=None)
        parser.add_argument('--depth', action='store_true', default=False,
                            help='expects RGB and depth as inputs')
        parser.add_argument('--debug', action='store_true', default=False,
                            help='no unnecessarily logging')
        parser.add_argument('--logsFlag', type=str, required=True)

        args = parser.parse_args()
        args.cuda = not args.no_cuda and torch.cuda.is_available()
        if args.cuda:
                try:
                        args.gpu_ids = [int(s) for s in args.gpu_ids.split(',')]
                except ValueError:
                        raise ValueError('Argument --gpu_ids must be a comma-separated list of integers only')

        if args.sync_bn is None:
                if args.cuda and len(args.gpu_ids) > 1:
                        args.sync_bn = True
                else:
                        args.sync_bn = False

        # default settings for epochs, batch_size and lr
        if args.epochs is None:
                epoches = {
                        'coco': 30,
                        'cityscapes': 200,
                        'pascal': 50,
                        'small_obstacle': 30
                }
                args.epochs = epoches[args.dataset.lower()]

        if args.batch_size is None:
                args.batch_size = 4 * len(args.gpu_ids)

        if args.test_batch_size is None:
                args.test_batch_size = args.batch_size

        if args.lr is None:
                lrs = {
                        'coco': 0.1,
                        'cityscapes': 0.01,
                        'pascal': 0.007,
                        'small_obstacle': 0.01,
                        'lnf': 0.01
                }
                args.lr = lrs[args.dataset.lower()] / (4 * len(args.gpu_ids)) * args.batch_size


        if args.checkname is None:
                args.checkname = 'deeplab-'+str(args.backbone)
        torch.manual_seed(args.seed)
        trainer = Trainer(args)
        print('Starting Epoch:', trainer.args.start_epoch)
        print('Total Epoches:', trainer.args.epochs)

        if args.mode=="train":
                for epoch in range(trainer.args.start_epoch, trainer.args.epochs):
                        trainer.training(epoch)
                        if not trainer.args.no_val and epoch % args.eval_interval == (args.eval_interval - 1):
                                trainer.validation(epoch)

        elif args.mode=="val" or args.mode=="test":

                for epoch in range(trainer.args.start_epoch, trainer.args.epochs):

                        trainer.validation(epoch)
                        break

        trainer.writer.close()

if __name__ == "__main__":
        main()
