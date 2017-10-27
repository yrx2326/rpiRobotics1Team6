#!/usr/bin/env python
import rospy
import cv2
import io
import numpy as np
from cv_bridge import CvBridge, CvBridgeError
from sensor_msgs.msg import Image, CompressedImage, CameraInfo
from sensor_msgs.srv import SetCameraInfo, SetCameraInfoResponse
from picamera import PiCamera
from picamera.array import PiRGBArray
import time
import signal
import sys
import rospkg
import os.path
import yaml
from duckietown_msgs.msg import BoolStamped, WheelsCmdStamped
import thread

class CameraNode(object):
    def __init__(self):
        self.node_name = rospy.get_name()
        rospy.loginfo("[%s] Initializing......" %(self.node_name))


        self.framerate_high = self.setupParam("~framerate_high",30.0)
        self.framerate_low = self.setupParam("~framerate_low",15.0)
        self.res_w = self.setupParam("~res_w",640)
        self.res_h = self.setupParam("~res_h",480)

        self.image_msg = CompressedImage()

        # Setup PiCamera

        self.camera = PiCamera()
        self.framerate = self.framerate_high # default to high
        self.camera.framerate = self.framerate
        self.camera.resolution = (self.res_w,self.res_h)


        # For intrinsic calibration
        rospack = rospkg.RosPack()
        self.config = self.setupParam("~config","baseline")
        self.cali_file_folder = rospack.get_path('duckietown') + "/config/" + self.config + "/calibration/camera_intrinsic/"
    
        self.frame_id = rospy.get_namespace().strip('/') + "/camera_optical_frame"

        self.has_published = False
        self.pub_img= rospy.Publisher("~image/compressed",CompressedImage,queue_size=1)
        self.sub_switch_high = rospy.Subscriber("~framerate_high_switch", BoolStamped, self.cbSwitchHigh, queue_size=1)

        # Create service (for camera_calibration)
        self.srv_set_camera_info = rospy.Service("~set_camera_info",SetCameraInfo,self.cbSrvSetCameraInfo)

        self.stream = io.BytesIO()

        # for wheels callback
        self.wheels_cmd_executed = rospy.Subscriber("~wheels_cmd_executed", WheelsCmdStamped, self.wheels_cmd_cb, queue_size=1)
 
#self.camera.exposure_mode = 'off'
       # self.camera.awb_mode = 'off'

        self.is_shutdown = False
        self.update_framerate = False
        # Setup timer
        rospy.loginfo("[%s] Initialized." %(self.node_name))

        self.loop_complete = False

    def wheels_cmd_cb(self, wheels_cmd_msg):
        rospy.loginfo("wheels command executed")
        self.loop_complete = True
        #self.grab_one_image(self.stream,self.pub_img)

    def cbSwitchHigh(self, switch_msg):
        print switch_msg
        if switch_msg.data and self.framerate != self.framerate_high:
            self.framerate = self.framerate_high
            self.update_framerate = True
        elif not switch_msg.data and self.framerate != self.framerate_low:
            self.framerate = self.framerate_low
            self.update_framerate = True

    def capture_image(self):
        gen =  self.grabAndPublish(self.stream,self.pub_img)
        try:
            pass
            #self.camera.capture_sequence(gen,'jpeg',use_video_port=True,splitter_port=0)
        except StopIteration:
            pass
        self.camera.framerate = self.framerate
        self.update_framerate=False
 
    def startCapturing(self):
        self.loop_complete = True
        rospy.loginfo("[%s] Start capturing." %(self.node_name))
        while not self.is_shutdown and not rospy.is_shutdown():
            '''if not self.loop_complete:
                rospy.loginfo("loop not complete - passing")
                pass
            '''
            rospy.loginfo("started startCapturing")
            gen =  self.grabAndPublish(self.stream,self.pub_img)
            try:
                self.camera.capture_sequence(gen,'jpeg',use_video_port=True,splitter_port=0)
            except StopIteration:
                pass
            print "updating framerate"
            self.camera.framerate = self.framerate
            self.update_framerate=False

        self.camera.close()
        rospy.loginfo("[%s] Capture Ended." %(self.node_name))

    def grabAndPublish(self,stream,publisher):
        rospy.loginfo("Started grabAndPublish")
        while not self.update_framerate and not self.is_shutdown and not rospy.is_shutdown(): 
            yield stream
            # Construct image_msg
            # Grab image from stream
            stamp = rospy.Time.now()
            stream.seek(0)
            stream_data = stream.getvalue()
            # Generate compressed image
            image_msg = CompressedImage()
            image_msg.format = "jpeg"
            image_msg.data = stream_data

            image_msg.header.stamp = stamp
            image_msg.header.frame_id = self.frame_id
            publisher.publish(image_msg)

            # log
            rospy.loginfo("camera_logging published image")
                        
            # Clear stream
            stream.seek(0)
            stream.truncate()
            
            if not self.has_published:
                rospy.loginfo("[%s] Published the first image." %(self.node_name))
                self.has_published = True

            rospy.sleep(rospy.Duration.from_sec(0.001))
            self.loop_complete = False

    def grab_one_image(self, stream, publisher):
        yield stream
        # Construct image_msg
        # Grab image from stream
        stamp = rospy.Time.now()
        stream.seek(0)
        stream_data = stream.getvalue()
        # Generate compressed image
        image_msg = CompressedImage()
        image_msg.format = "jpeg"
        image_msg.data = stream_data

        image_msg.header.stamp = stamp
        image_msg.header.frame_id = self.frame_id
        publisher.publish(image_msg)

        # log
        rospy.loginfo("camera_logging published image")
                    
        # Clear stream
        stream.seek(0)
        stream.truncate()
        
        if not self.has_published:
            rospy.loginfo("[%s] Published the first image." %(self.node_name))
            self.has_published = True


    def setupParam(self,param_name,default_value):
        value = rospy.get_param(param_name,default_value)
        rospy.set_param(param_name,value) #Write to parameter server for transparancy
        rospy.loginfo("[%s] %s = %s " %(self.node_name,param_name,value))
        return value

    def onShutdown(self):
        rospy.loginfo("[%s] Closing camera." %(self.node_name))
        self.is_shutdown=True
        rospy.loginfo("[%s] Shutdown." %(self.node_name))
        self.camera.close()


    def cbSrvSetCameraInfo(self,req):
        # TODO: save req.camera_info to yaml file
        rospy.loginfo("[cbSrvSetCameraInfo] Callback!")
        filename = self.cali_file_folder + rospy.get_namespace().strip("/") + ".yaml"
        response = SetCameraInfoResponse()
        response.success = self.saveCameraInfo(req.camera_info,filename)
        response.status_message = "Write to %s" %filename #TODO file name
        return response

    def saveCameraInfo(self, camera_info_msg, filename):
        # Convert camera_info_msg and save to a yaml file
        rospy.loginfo("[saveCameraInfo] filename: %s" %(filename))
        file = open(filename, 'w')

        # Converted from camera_info_manager.py
        calib = {'image_width': camera_info_msg.width,
        'image_height': camera_info_msg.height,
        'camera_name': rospy.get_name().strip("/"), #TODO check this
        'distortion_model': camera_info_msg.distortion_model,
        'distortion_coefficients': {'data': camera_info_msg.D, 'rows':1, 'cols':5},
        'camera_matrix': {'data': camera_info_msg.K, 'rows':3, 'cols':3},
        'rectification_matrix': {'data': camera_info_msg.R, 'rows':3, 'cols':3},
        'projection_matrix': {'data': camera_info_msg.P,'rows':3, 'cols':4}}
        
        rospy.loginfo("[saveCameraInfo] calib %s" %(calib))

        try:
            rc = yaml.safe_dump(calib, file)
            return True
        except IOError:
            return False

if __name__ == '__main__': 
    rospy.init_node('camera_logging',anonymous=False)
    camera_logging_node = CameraNode()
    rospy.on_shutdown(camera_logging_node.onShutdown)
    thread.start_new_thread(camera_logging_node.startCapturing, ())
    rospy.loginfo("camera init started")
    #camera_logging_node.grab_one_image(camera_logging_node.stream, camera_logging_node.pub_img)
    rospy.loginfo("camera init finished")
    rospy.spin()
