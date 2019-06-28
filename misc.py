import os
import random
random.seed(10)
from shutil import copy

images = []
path='/scratch/ash/data_run/downtown_data_run/'
new_path='/scratch/ash/data_batch/test/'
for folder in os.listdir(path):
	path2 = os.path.join(path, folder, 'image')
	for image in os.listdir(path2):
		images.append(path2 + '/' + image)

random.shuffle(images)

len_dataset = len(images)

dataset_path = {}
dataset_path['train'] = images[:int(0.7 * len_dataset)]
dataset_path['val'] = images[int(0.7 * len_dataset):int(0.9 * len_dataset)]
dataset_path['test'] = images[int(0.9 * len_dataset):]
print("Dataset found ... Train Size: {}, Val Size: {}, Test Size: {}".format(len(dataset_path['train']),
																					 len(dataset_path['val']),
																					 len(dataset_path['test'])))

for files in dataset_path['test']:
	target=files.split('image')
	target=target[0]+'segmentation'+target[1]
	img_path=new_path+'image/'
	label_path=new_path+'segmentation/'
	if not os.path.exists(img_path):
		os.makedirs(img_path)
		os.makedirs(label_path)
	copy(files,img_path)
	copy(target,label_path)

		
		