# USAGE
# NOTE: When using an input video file, speeds will be inaccurate
# because OpenCV can't throttle FPS according to the framerate of the
# video. This script is for development purposes only.
#
# python speed_estimation_dl.py --conf config/config.json --input sample_data/cars.mp4

# inform the user about framerates and speeds
print("[INFO] NOTE: When using an input video file, speeds will be " \
	"inaccurate because OpenCV can't throttle FPS according to the " \
	"framerate of the video. This script is for development purposes " \
	"only.")

# import the necessary packages
from tracker.centroidtracker import CentroidTracker
from tracker.trackableobject import TrackableObject
from tracker.utils import Conf
from imutils.video import VideoStream
from imutils.io import TempFile
from imutils.video import FPS
from datetime import datetime
from threading import Thread
import numpy as np
import argparse
import imutils
import dlib
import time
import cv2
import os

# construct the argument parser and parse the arguments
ap = argparse.ArgumentParser()
ap.add_argument("-c", "--conf", required=True,
	help="Path to the input configuration file")
ap.add_argument("-i", "--input", required=True,
	help="Path to the input video file")
args = vars(ap.parse_args())

# load the configuration file
conf = Conf(args["conf"])

# initialize the list of class labels MobileNet SSD was trained to
# detect
CLASSES = ["background", "aeroplane", "bicycle", "bird", "boat",
	"bottle", "bus", "car", "cat", "chair", "cow", "diningtable",
	"dog", "horse", "motorbike", "person", "pottedplant", "sheep",
	"sofa", "train", "tvmonitor"]

# load our serialized model from disk
print("[INFO] loading model...")
net = cv2.dnn.readNetFromCaffe(conf["prototxt_path"],
	conf["model_path"])
#net.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)

# initialize the video stream and allow the camera sensor to warmup
print("[INFO] warming up camera...")
vs = VideoStream(src=0).start()
#vs = cv2.VideoCapture(args["input"])
time.sleep(2.0)

# initialize the frame dimensions (we'll set them as soon as we read
# the first frame from the video)
H = None
W = None

# instantiate our centroid tracker, then initialize a list to store
# each of our dlib correlation trackers, followed by a dictionary to
# map each unique object ID to a TrackableObject
ct = CentroidTracker(maxDisappeared=conf["max_disappear"],
	maxDistance=conf["max_distance"])
trackers = []
trackableObjects = {}

# keep the count of total number of frames
totalFrames = 0

# initialize the log file
logFile = None

# initialize the list of various points used to calculate the avg of
# the vehicle speed
points = [("A", "B"), ("B", "C"), ("C", "D")]

# start the frames per second throughput estimator
fps = FPS().start()

# loop over the frames of the stream
while True:
	# grab the next frame from the stream, store the current
	# timestamp, and store the new date
	frame  = vs.read()
	now = datetime.now()
	newDate = now.strftime("%m-%d-%y")
	ts = (now - datetime(1970, 1, 1)).total_seconds()

	# check if the frame is None, if so, break out of the loop
	if frame is None:
		break

	# if the log file has not been created or opened
	if logFile is None:
		# build the log file path and create/open the log file
		logPath = os.path.join(conf["output_path"], conf["csv_name"])
		logFile = open(logPath, mode="a")

		# set the file pointer to end of the file
		pos = logFile.seek(0, os.SEEK_END)

		logFile.write("Year,Month,Day,Time (in MPH),Speed\n")
			
	# resize the frame
	frame = imutils.resize(frame, width=conf["frame_width"])
	rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

	# if the frame dimensions are empty, set them
	if W is None or H is None:
		(H, W) = frame.shape[:2]
		meterPerPixel = float(conf["distance"])/float(W)
		print("H: {}, W: {}, distance: {}, meterPerPixel: {}".format(H, W, conf["distance"], meterPerPixel))

	# initialize our list of bounding box rectangles returned by
	# either (1) our object detector or (2) the correlation trackers
	rects = []

	# check to see if we should run a more computationally expensive
	# object detection method to aid our tracker
	if totalFrames % conf["track_object"] == 0:
		# initialize our new set of object trackers
		trackers = []

		# convert the frame to a blob and pass the blob through the
		# network and obtain the detections
		blob = cv2.dnn.blobFromImage(frame, size=(300, 300),
			ddepth=cv2.CV_8U)
		net.setInput(blob, scalefactor=1.0/127.5, mean=[127.5,
			127.5, 127.5])
		detections = net.forward()

		# loop over the detections
		for i in np.arange(0, detections.shape[2]):
			# extract the confidence (i.e., probability) associated
			# with the prediction
			confidence = detections[0, 0, i, 2]

			# filter out weak detections by ensuring the `confidence`
			# is greater than the minimum confidence
			if confidence > conf["confidence"]:
				# extract the index of the class label from the
				# detections list
				idx = int(detections[0, 0, i, 1])

				# if the class label is not a car, ignore it
				if CLASSES[idx] != "person":
					continue

				# compute the (x, y)-coordinates of the bounding box
				# for the object
				box = detections[0, 0, i, 3:7] * np.array([W, H, W, H])
				(startX, startY, endX, endY) = box.astype("int")

				# construct a dlib rectangle object from the bounding
				# box coordinates and then start the dlib correlation
				# tracker
				tracker = dlib.correlation_tracker()
				rect = dlib.rectangle(startX, startY, endX, endY)
				tracker.start_track(rgb, rect)

				# add the tracker to our list of trackers so we can
				# utilize it during skip frames
				trackers.append(tracker)

	# otherwise, we should utilize our object *trackers* rather than
	# object *detectors* to obtain a higher frame processing
	# throughput
	else:
		# loop over the trackers
		for tracker in trackers:
			# update the tracker and grab the updated position
			tracker.update(rgb)
			pos = tracker.get_position()

			# unpack the position object
			startX = int(pos.left())
			startY = int(pos.top())
			endX = int(pos.right())
			endY = int(pos.bottom())

			# add the bounding box coordinates to the rectangles list
			rects.append((startX, startY, endX, endY))

	# use the centroid tracker to associate the (1) old object
	# centroids with (2) the newly computed object centroids
	objects = ct.update(rects)

	# loop over the tracked objects
	for (objectID, centroid) in objects.items():
		# check to see if a trackable object exists for the current
		# object ID
		to = trackableObjects.get(objectID, None)

		# if there is no existing trackable object, create one
		if to is None:
			to = TrackableObject(objectID, centroid)

		# otherwise, if there is a trackable object and its speed has
		# not yet been estimated then estimate it
		elif not to.estimated:
			# check if the direction of the object has been set, if
			# not, calculate it, and set it
			if to.direction is None:
				y = [c[0] for c in to.centroids]
				direction = centroid[0] - np.mean(y)
				to.direction = direction

			# if the direction is positive (indicating the object
			# is moving from left to right)
			if to.direction > 0:
				# check to see if timestamp has been noted for
				# point A
				if to.timestamp["A"] == 0 :
					# if the centroid's x-coordinate is greater than
					# the corresponding point then set the timestamp
					# as current timestamp and set the position as the
					# centroid's x-coordinate
					if centroid[0] > conf["speed_estimation_zone"]["A"]:
						to.timestamp["A"] = ts
						to.position["A"] = centroid[0]
						
				# check to see if timestamp has been noted for
				# point B
				elif to.timestamp["B"] == 0:
					# if the centroid's x-coordinate is greater than
					# the corresponding point then set the timestamp
					# as current timestamp and set the position as the
					# centroid's x-coordinate
					if centroid[0] > conf["speed_estimation_zone"]["B"]:
						to.timestamp["B"] = ts
						to.position["B"] = centroid[0]
						
				# check to see if timestamp has been noted for
				# point C
				elif to.timestamp["C"] == 0:
					# if the centroid's x-coordinate is greater than
					# the corresponding point then set the timestamp
					# as current timestamp and set the position as the
					# centroid's x-coordinate
					if centroid[0] > conf["speed_estimation_zone"]["C"]:
						to.timestamp["C"] = ts
						to.position["C"] = centroid[0]
						
				# check to see if timestamp has been noted for
				# point D
				elif to.timestamp["D"] == 0:
					# if the centroid's x-coordinate is greater than
					# the corresponding point then set the timestamp
					# as current timestamp, set the position as the
					# centroid's x-coordinate, and set the last point
					# flag as True
					if centroid[0] > conf["speed_estimation_zone"]["D"]:
						to.timestamp["D"] = ts
						to.position["D"] = centroid[0]
						to.lastPoint = True
						
			# if the direction is negative (indicating the object
			# is moving from right to left)
			elif to.direction < 0:
				# check to see if timestamp has been noted for
				# point D
				if to.timestamp["D"] == 0 :
					# if the centroid's x-coordinate is lesser than
					# the corresponding point then set the timestamp
					# as current timestamp and set the position as the
					# centroid's x-coordinate
					if centroid[0] < conf["speed_estimation_zone"]["D"]:
						to.timestamp["D"] = ts
						to.position["D"] = centroid[0]
						
				# check to see if timestamp has been noted for
				# point C
				elif to.timestamp["C"] == 0:
					# if the centroid's x-coordinate is lesser than
					# the corresponding point then set the timestamp
					# as current timestamp and set the position as the
					# centroid's x-coordinate
					if centroid[0] < conf["speed_estimation_zone"]["C"]:
						to.timestamp["C"] = ts
						to.position["C"] = centroid[0]
						
				# check to see if timestamp has been noted for
				# point B
				elif to.timestamp["B"] == 0:
					# if the centroid's x-coordinate is lesser than
					# the corresponding point then set the timestamp
					# as current timestamp and set the position as the
					# centroid's x-coordinate
					if centroid[0] < conf["speed_estimation_zone"]["B"]:
						to.timestamp["B"] = ts
						to.position["B"] = centroid[0]
						
				# check to see if timestamp has been noted for
				# point A
				elif to.timestamp["A"] == 0:
					# if the centroid's x-coordinate is lesser than
					# the corresponding point then set the timestamp
					# as current timestamp, set the position as the
					# centroid's x-coordinate, and set the last point
					# flag as True
					if centroid[0] < conf["speed_estimation_zone"]["A"]:
						to.timestamp["A"] = ts
						to.position["A"] = centroid[0]
						to.lastPoint = True

			# check to see if the vehicle is past the last point and
			# the vehicle's speed has not yet been estimated, if yes,
			# then calculate the vehicle speed and log it if it's
			# over the limit
			if to.lastPoint and not to.estimated:
				# initialize the list of estimated speeds
				estimatedSpeeds = []

				# loop over all the pairs of points and estimate the
				# vehicle speed
				for (i, j) in points:
					# calculate the distance in pixels
					d = to.position[j] - to.position[i]
					distanceInPixels = abs(d)

					# check if the distance in pixels is zero, if so,
					# skip this iteration
					if distanceInPixels == 0:
						continue

					# calculate the time in hours
					t = to.timestamp[j] - to.timestamp[i]
					timeInSeconds = abs(t)
					timeInHours = float(timeInSeconds / (60 * 60))

					speedInPixel = float(distanceInPixels / timeInHours) 
					speedInMeter = float(speedInPixel * meterPerPixel)
					speedinKM = float(speedInMeter / 1000)

					estimatedSpeeds.append(speedinKM)
					print("[INFO] Speed of point pair {}, {}: {}".format(i, j, speedinKM))


				# calculate the average speed
				to.calculate_speed(estimatedSpeeds)

				# set the object as estimated
				to.estimated = True

		# store the trackable object in our dictionary
		trackableObjects[objectID] = to

		# draw both the ID of the object and the centroid of the
		# object on the output frame
		text = "ID {}".format(objectID)
		cv2.putText(frame, text, (centroid[0] - 10, centroid[1] - 10)
			, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
		cv2.circle(frame, (centroid[0], centroid[1]), 4,
			(0, 255, 0), -1)

		# check if the object has not been logged
		if not to.logged:
			# check if the object's speed has been estimated and it
			# is higher than the speed limit
			if to.estimated and to.speedMPH > conf["speed_limit"]:
				# set the current year, month, day, and time
				year = now.strftime("%Y")
				month = now.strftime("%m")
				day = now.strftime("%d")
				time = now.strftime("%H:%M:%S")
				
				# log the event in the log file
				info = "{},{},{},{},{}\n".format(year, month,
					day, time, to.speedMPH)
				logFile.write(info)

				# set the object has logged
				to.logged = True

	# if the *display* flag is set, then display the current frame
	# to the screen and record if a user presses a key
	if conf["display"]:
		cv2.imshow("frame", frame)
		key = cv2.waitKey(1) & 0xFF

		# if the `q` key is pressed, break from the loop
		if key == ord("q"):
			break

	# increment the total number of frames processed thus far and
	# then update the FPS counter
	totalFrames += 1
	fps.update()

# stop the timer and display FPS information
fps.stop()
print("[INFO] elapsed time: {}".format(fps.elapsed()))
print("[INFO] approx. FPS: {}".format(fps.fps()))

# check if the log file object exists, if it does, then close it
if logFile is not None:
	logFile.close()

# close any open windows
cv2.destroyAllWindows()

# clean up
print("[INFO] cleaning up...")
vs.release()