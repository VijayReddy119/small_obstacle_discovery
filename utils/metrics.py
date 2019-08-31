import numpy as np
import scipy.ndimage as ndi

class Evaluator(object):
    def __init__(self, num_class):
        self.num_class = num_class
        self.confusion_matrix = np.zeros((self.num_class,)*2)
        self.idr_count = 0

    def Pixel_Accuracy(self):
        Acc = np.diag(self.confusion_matrix).sum() / self.confusion_matrix.sum()
        return Acc

    def Pixel_Accuracy_Class(self):
        Acc = np.diag(self.confusion_matrix) / self.confusion_matrix.sum(axis=1)
        Acc = np.nanmean(Acc)
        return Acc

    def Mean_Intersection_over_Union(self):
        MIoU = np.diag(self.confusion_matrix) / (
                    np.sum(self.confusion_matrix, axis=1) + np.sum(self.confusion_matrix, axis=0) -
                    np.diag(self.confusion_matrix))
        MIoU = np.nanmean(MIoU)
        return MIoU

    def Frequency_Weighted_Intersection_over_Union(self):
        freq = np.sum(self.confusion_matrix, axis=1) / np.sum(self.confusion_matrix)
        iu = np.diag(self.confusion_matrix) / (
                    np.sum(self.confusion_matrix, axis=1) + np.sum(self.confusion_matrix, axis=0) -
                    np.diag(self.confusion_matrix))

        FWIoU = (freq[freq > 0] * iu[freq > 0]).sum()
        return FWIoU

    def _generate_matrix(self, gt_image, pre_image):
        mask = (gt_image >= 0) & (gt_image < self.num_class)
        label = self.num_class * gt_image[mask].astype('int') + pre_image[mask]
        count = np.bincount(label, minlength=self.num_class**2)
        confusion_matrix = count.reshape(self.num_class, self.num_class)
        return confusion_matrix
    

    def pdr_metric(self,class_id):
        """
        Precision and recall metric for each class
         class_id=2 for small obstacle [0-off road,1-on road]
        """
        truth_mask=self.gt_labels==class_id
        pred_mask=self.pred_labels==class_id

        true_positive=(truth_mask & pred_mask)
        true_positive=np.count_nonzero(true_positive==True)

        total=np.count_nonzero(truth_mask==True)
        pred=np.count_nonzero(pred_mask==True)

        recall=float(true_positive/total)
        precision=float(true_positive/pred)
        return recall,precision


    def get_idr(self, pred, target, class_value, threshold=0.4):

        """Returns Instance Detection Ratio (IDR)
        for a given class, where class_id = numeric label of that class in segmentation target img
        Threshold is defined as minimum ratio of pixels between prediction and target above
        which an instance is defined to have been detected
        """
        pred = pred.squeeze()
        target = target.squeeze()

        pred_mask = pred == class_value
        target_mask = target == class_value
        instance_id, instance_num = ndi.label(target_mask)     # Return number of instances of given class present in target image
        count = 0

        if instance_num == 0:
            return 0.0

        for id in range(1, instance_num + 1):           # Background is given instance id zero
            x, y = np.where(instance_id == id)
            detection_ratio = np.count_nonzero(pred_mask[x, y]) / np.count_nonzero(target_mask[x, y])
            if detection_ratio >= threshold:
                count += 1

        idr = float(count / instance_num)
        self.idr_count += 1
        return idr


    def add_batch(self, gt_image, pre_image):
        assert gt_image.shape == pre_image.shape
        if len(self.gt_labels) == 0 and len(self.pred_labels) == 0:
            self.gt_labels=gt_image
            self.pred_labels=pre_image
        else:
            self.gt_labels=np.append(self.gt_labels,gt_image,axis=0)
            self.pred_labels=np.append(self.pred_labels,pre_image,axis=0)

        self.confusion_matrix += self._generate_matrix(gt_image, pre_image)


    def reset(self):
        self.confusion_matrix = np.zeros((self.num_class,) * 2)
        self.gt_labels=[] 
        self.pred_labels=[]
        self.idr_count = 0




