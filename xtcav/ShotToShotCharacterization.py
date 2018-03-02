#(c) Coded by Alvaro Sanchez-Gonzalez 2014

#Script for the retrieval of the pulses shot to shot
import os
import time
import psana
import numpy as np
import glob
import pdb
import IPython
import sys
import getopt
import math
import warnings
import Utils as xtu
import UtilsPsana as xtup
import SplittingUtils as su
import Constants
from DarkBackground import *
from LasingOffReference import *
from CalibrationPaths import *
from Metrics import *


class ShotToShotCharacterization(object):

    """
    Class that can be used to reconstruct the full X-Ray power time profile for single or multiple bunches, relying on the presence of a dark background reference, and a lasing off reference. (See GenerateDarkBackground and Generate LasingOffReference for more information)
    Attributes:
        calibrationpath (str): Custom calibration directory in case the default is not intended to be used.
        medianfilter (int): Number of neighbours for median filter (If not set, the value that was used for the lasing off reference will be used).
        snrfilter (float): Number of sigmas for the noise threshold (If not set, the value that was used for the lasing off reference will be used).
        roiwaistthres (float): ratio with respect to the maximum to decide on the waist of the XTCAV trace (If not set, the value that was used for the lasing off reference will be used).
        roiexpand (float): number of waists that the region of interest around will span around the center of the trace (If not set, the value that was used for the lasing off reference will be used).
        islandsplitmethod (str): island splitting algorithm. Set to 'scipylabel' or 'contourLabel'  The defaults parameter is then one used for the lasing off reference or 'scipylabel'.
    """

    def __init__(self, 
        experiment='',
        runs='',
        num_bunches = None,
        start_image = 0,
        medianfilter = None,
        snrfilter = None,
        roiwaistthres = None,
        roiexpand = None,
        islandsplitmethod=None,
        islandsplitpar1=None,
        islandsplitpar2=None,
        darkreferencepath=None,
        lasingoffreferencepath=None,
        calpath=''
        ):
            
        #Handle warnings
        warnings.filterwarnings('always',module='Utils',category=UserWarning)
        warnings.filterwarnings('ignore',module='Utils',category=RuntimeWarning, message="invalid value encountered in divide")
        
        self.experiment = experiment                #Experiment label
        self.runs = runs                            #Run numbers
        self.num_bunches = num_bunches              #Number of bunches
        self.start_image = start_image
        self.medianfilter = medianfilter            #Number of neighbours for median filter
        self.snrfilter = snrfilter                  #Number of sigmas for the noise threshold
        self.roiwaistthres = roiwaistthres          #Parameter for the roi location
        self.roiexpand = roiexpand                  #Parameter for the roi location
        self.islandsplitmethod = islandsplitmethod  #Method for island splitting
        self.islandsplitpar1 = islandsplitpar1
        self.islandsplitpar2 = islandsplitpar2
        
        self.darkreferencepath = darkreferencepath  #Dark reference file path
        self.lasingoffreferencepath = lasingoffreferencepath        #Lasing off reference file path 
        self.calpath = calpath
        
        self._envset = False
        if experiment and runs:
            self.SetDataSource()


    def SetExperiment(self, experiment, runs):
        self.experiment = experiment
        self.runs = runs
        self.SetDataSource()

            
    def SetDataSource(self, datasource=None):
        if datasource:
            self.experiment = datasource.env().experiment()
            self._datasource = psana.DataSource(datasource.env().jobName())
        else:
            self._datasource = psana.DataSource("exp=%s:run=%s:idx" % (self.experiment, self.runs))

        self.SetEnvironment()
        self.LoadDarkReference()
        self.LoadLasingOffReference()


    def SetEnvironment(self):
        self._env = self._datasource.env()
        self._xtcav_camera = psana.Detector(Constants.SRC)
        self._ebeam_data = psana.Detector(Constants.EBEAM)
        self._gasdetector_data = psana.Detector(Constants.GAS_DETECTOR)
        self._ebeam = None
        self._gasdetector = None

        self._currentrun = self._datasource.runs().next()

        self._roixtcav, first_image = xtup.GetXTCAVImageROI(self._currentrun, self._xtcav_camera, start = self.start_image)
        self._global_calibration, first_image = xtup.GetGlobalXTCAVCalibration(self._currentrun, self._xtcav_camera, start=first_image)
        self._saturation_value, self.start_image = xtup.GetCameraSaturationValue(self._currentrun, self._xtcav_camera, start=first_image)
        self._envset = True


    def LoadDarkReference(self):
        """
        Method that loads the dark reference. This method is called automatically and should not be called by the user unless he has a knowledge of the operation done by this class internally.    
        """
        self._darkreference = None

        if not self.darkreferencepath:
            if not self._envset:
                #warnings.warn_explicit('Dark reference not loaded. Must set datasource or supply darkreferencepath',UserWarning,'XTCAV',0)
                return 

            cp=CalibrationPaths(self._env, self.calpath)       
            self.darkreferencepath=cp.findCalFileName('pedestals', self._currentrun)
            #If we could not find it, we just wont use it, and return False
            if not self.darkreferencepath:
                warnings.warn_explicit('Dark reference for run %d not found, image will not be background substracted' % self._currentevent.run(),UserWarning,'XTCAV',0)
                return    
        
        self._darkreference = DarkBackground.Load(self.darkreferencepath)

                
    def LoadLasingOffReference(self):
        """
        Method that loads the lasing off reference. This method is called automatically and should not be called by the user unless he has a knowledge of the operation done by this class internally.
        """
        self._lasingoffreference = None

        if not self.lasingoffreferencepath:
            if not self._envset:
                #warnings.warn_explicit('Lasing off reference not loaded. Must set datasource or supply lasingoffreferencepath',UserWarning, 'XTCAV',0)
                return 
            cp=CalibrationPaths(self._env,self.calpath)     
            self.lasingoffreferencepath = cp.findCalFileName('lasingoffreference',  self._currentrun)
            
            #If we could not find it, we load default parameters, and return False
            if not self.lasingoffreferencepath:
                warnings.warn_explicit('Lasing off reference for run %d not found, using set or default values for image processing' % self._currentevent.run(),UserWarning,'XTCAV',0)
                self.LoadDefaultProcessingParameters()            
                return

        self._lasingoffreference = LasingOffReference.Load(self.lasingoffreferencepath)
        self.LoadLasingOffReferenceParameters()

            
    def LoadDefaultProcessingParameters(self):
        """
        Method that sets some standard processing parameters in case they have not been explicitly set by the user and could not been retrieved from the lasing off reference. This method is called automatically and should not be called by the user unless he has a knowledge of the operation done by this class internally.             
        """
        if not self.num_bunches:
            self.num_bunches=1
        if not self.medianfilter:
            self.medianfilter=3
        if not self.snrfilter:
            self.snrfilter=10
        if not self.roiwaistthres:
            self.roiwaistthres=0.2
        if not self.roiexpand:
            self.roiexpand=2.5    
        if not self.islandsplitmethod:
            self.islandsplitmethod='scipylabel'       
        if not self.islandsplitpar1:        
            self.islandsplitpar1=3.0
        if not self.islandsplitpar2:        
            self.islandsplitpar2=5.0


    def LoadLasingOffReferenceParameters(self):
        """
        Method that sets processing parameters from the lasing off reference in case they have not been explicitly set by the user (except for the number of bunches. That one is must match). This method is called automatically and should not be called by the user unless he has a knowledge of the operation done by this class internally.             
        """
        if self.num_bunches and self.num_bunches != self._lasingoffreference.parameters.num_bunches:
            warnings.warn_explicit('Number of bunches input (%d) differs from number of bunches found in lasing off reference (%d). Overwriting input value.' % (self.num_bunches,self._lasingoffreference.parameters.num_bunches) ,UserWarning,'XTCAV',0)
        self.num_bunches=self._lasingoffreference.parameters.num_bunches
        if not self.medianfilter:
            self.medianfilter=self._lasingoffreference.parameters.medianfilter
        if not self.snrfilter:
            self.snrfilter=self._lasingoffreference.parameters.snrfilter
        if not self.roiwaistthres:
            self.roiwaistthres=self._lasingoffreference.parameters.roiwaistthres
        if not self.roiexpand:
            self.roiexpand=self._lasingoffreference.parameters.roiexpand
        if not self.darkreferencepath:
            self.darkreferencepath=self._lasingoffreference.parameters.darkreferencepath
        if not self.islandsplitmethod:
            self.islandsplitmethod=self._lasingoffreference.parameters.islandsplitmethod
        if not self.islandsplitpar1:        
            self.islandsplitpar1=self._lasingoffreference.parameters.islandsplitpar1
        if not self.islandsplitpar2:        
            self.islandsplitpar2=self._lasingoffreference.parameters.islandsplitpar2 

                           
    def processEvent(self,evt):
        """
        Args:
            evt (psana event): relevant event to retrieve information from
            
        Returns:
            True: All the input form detectors necessary for a good reconstruction are present in the event. 
            False: The information from some detectors is missing for that event. It may still be possible to get information.
        """
        if not self._envset:
            warnings.warn_explicit('Environment not set. Must set datasource or experiment and run number',UserWarning,'XTCAV',0)
            return 

        img = self._xtcav_camera.image(evt)

        if img is None: 
            return False

        self._rawimage = img
        self._currentevent = evt
        #Reset image results
        self._pulse_characterization = None
        self._image_profile = None
        self._processed_image = None

        self._ebeam = self._ebeam_data.get(evt)
        self._gasdetector = self._gasdetector_data.get(evt)  

        self.setImageProfile()
        if not self._image_profile:
            warnings.warn_explicit('Cannot create image profile',UserWarning,'XTCAV',0)
            return False

        if not self._lasingoffreference:
            warnings.warn_explicit('Cannot perform analysis without lasing off reference',UserWarning,'XTCAV',0)
            return False

        #Using all the available data, perform the retrieval for that given shot        
        self._pulse_characterization = xtu.ProcessLasingSingleShot(self._image_profile, self._lasingoffreference.averaged_profiles) 
        return True if self._pulse_characterization else False


    def setImageProfile(self):
        """
        Method that runs the first step of the reconstruction, which consists of getting statistics from the XTCAV trace. This method is called automatically and should not be called by the user unless he has a knowledge of the operation done by this class internally. 
        """
        if np.max(self._rawimage)>=self._saturation_value : #Detection if the image is saturated, we skip if it is
            warnings.warn_explicit('Saturated Image. Skipping...',UserWarning,'XTCAV',0)
            return

        shot_to_shot = xtup.GetShotToShotParameters(self._ebeam, self._gasdetector, self._currentevent.get(psana.EventId)) #Obtain the shot to shot parameters necessary for the retrieval of the x and y axis in time and energy units
        if not shot_to_shot.valid: #If the information is not good, we skip the event
            return                              
        #Subtract the dark background, taking into account properly possible different ROIs
        #Only if the reference is present
        if self._darkreference:        
            img, ROI = xtu.SubtractBackground(self._rawimage, self._roixtcav, self._darkreference)  
        else:
            ROI = self._roixtcav
            img = self._rawimage
            
        img, contains_data = xtu.DenoiseImage(img, self.medianfilter, self.snrfilter)                    #Remove noise from the image and normalize it
        if not contains_data:                                        #If there is nothing in the image we skip the event  
            return 

        img, ROI = xtu.FindROI(img, ROI, self.roiwaistthres, self.roiexpand)                  #Crop the image, the ROI struct is changed. It also add an extra dimension to the image so the array can store multiple images corresponding to different bunches
        if ROI.xN < Constants.MIN_ROI_SIZE or ROI.yN < Constants.MIN_ROI_SIZE:
            print 'ROI too small',ROI.xN,ROI.yN
            return 

        processed_image = su.SplitImage(img, self.num_bunches, self.islandsplitmethod, self.islandsplitpar1, self.islandsplitpar2)

        image_stats = xtu.ProcessXTCAVImage(processed_image, ROI)          #Obtain the different properties and profiles from the trace        
        
        physical_units = xtu.CalculatePhyscialUnits(ROI,[image_stats[0].xCOM,image_stats[0].yCOM], shot_to_shot, self._global_calibration)   
        if not physical_units.valid:
            return 

        #If the step in time is negative, we mirror the x axis to make it ascending and consequently mirror the profiles     
        if physical_units.xfsPerPix < 0:
            physical_units = physical_units._replace(xfs = physical_units.xfs[::-1])
            for j in range(self._num_bunches):
                image_stats[j] = image_stats[j]._replace(xProfile = image_stats[j].xProfile[::-1])
                image_stats[j] = image_stats[j]._replace(yCOMslice = image_stats[j].yCOMslice[::-1])
                image_stats[j] = image_stats[j]._replace(yRMSslice = image_stats[j].yRMSslice[::-1])
                
        #Save the results of the step 2
        self._image_profile = ImageProfile(image_stats, ROI, shot_to_shot, physical_units)
        self._processed_image = processed_image

        
    def GetPhysicalUnits(self):
        """
        Method which returns a dictionary based list with the physical units for the cropped image

        Returns: 
            PhysicalUnits: List with the results
                'yMeVPerPix':         Number of MeV per pixel for the vertical axis of the image
                'xfsPerPix':          Number of fs per pixel for the horizontal axis of the image
                'xfs':                Horizontal axis of the image in fs
                'yMeV':               Vertical axis of the image in MeV
        """
    
        if not self._image_profile:
            warnings.warn_explicit('Image profile not created for current event due to issues with image',UserWarning,'XTCAV',0)
            return None
        
        return self._image_profile.physical_units               
        
    def GetFullResults(self):
        """
        Method which returns a dictionary based list with the full results of the characterization

        Returns: 
            PulseCharacterization: List with the results
                't':                           Master time vector in fs
                'powerECOM':                    Retrieved power in GW based on ECOM
                'powerERMS':                    Retrieved power in GW based on ERMS
                'powerAgreement':               Agreement between the two intensities
                'bunchdelay':                   Delay from each bunch with respect to the first one in fs
                'bunchdelaychange':             Difference between the delay from each bunch with respect to the first one in fs and the same form the non lasing reference
                'xrayenergy':                   Total x-ray energy from the gas detector in J
                'lasingenergyperbunchECOM':     Energy of the XRays generated from each bunch for the center of mass approach in J
                'lasingenergyperbunchERMS':     Energy of the XRays generated from each bunch for the dispersion approach in J
                'bunchenergydiff':              Distance in energy for each bunch with respect to the first one in MeV
                'bunchenergydiffchange':        Comparison of that distance with respect to the no lasing
                'lasingECurrent':               Electron current for the lasing trace (In #electrons/s)
                'nolasingECurrent':             Electron current for the no lasing trace (In #electrons/s)
                'lasingECOM':                   Lasing energy center of masses for each time in MeV
                'nolasingECOM':                 No lasing energy center of masses for each time in MeV
                'lasingERMS':                   Lasing energy dispersion for each time in MeV
                'nolasingERMS':                 No lasing energy dispersion for each time in MeV
                'num_bunches':                           Number of bunches
        """
        if not self._pulse_characterization:
            warnings.warn_explicit('Pulse characterization not created for current event due to issues with image',UserWarning,'XTCAV',0)
            
        return self._pulse_characterization       
            
    def PulseDelay(self,method='RMSCOM'):    
        """
        Method which returns the time of lasing for each bunch based on the x-ray reconstruction. They delays are referred to the center of mass of the total current. The order of the delays goes from higher to lower energy electron bunches.
        Args:
            method (str): method to use to obtain the power profile. 'RMS', 'COM' or 'RMSCOM' (Average of both)
        Returns: 
            List of the delays for each bunch.
        """
        if not self._pulse_characterization:
            warnings.warn_explicit('Pulse characterization not created for current event due to issues with image. ' +\
                'Cannot construct pulse delay',UserWarning,'XTCAV',0)
            return None
            
        num_bunches = self._pulse_characterization.num_bunches
        if num_bunches < 1:
            return np.zeros((num_bunches), dtype=np.float64)
        
                  
        peakpos=np.zeros((num_bunches), dtype=np.float64);
        for j in range(num_bunches):
            t = self._pulse_characterization.t + self._pulse_characterization.bunchdelay[j]
            if method == 'RMS':
                power = self._pulse_characterization.powerERMS[j]
            elif method=='COM':
                power = self._pulse_characterization.powerECOM[j]
            elif method=='RMSCOM':
                power = (self._pulse_characterization.powerECOM[j] + self._pulse_characterization.powerERMS[j])/2
            else:
                warnings.warn_explicit('Method %s not supported' % (method),UserWarning,'XTCAV',0)
                return None      
            #quadratic fit around 5 pixels method
            central=np.argmax(power)
            try:
                fit=np.polyfit(t[central-2:central+3],power[central-2:central+3],2)
                peakpos[j]=-fit[1]/(2*fit[0])
            except:
                return None 
            
        return peakpos
            
    def PulseFWHM(self,method='RMSCOM'):    
        """
        Method which returns the FWHM of the pulse generated by each bunch in fs. It uses the power profile. The order of the widths goes from higher to lower energy electron bunches.
        Args:
            method (str): method to use to obtain the power profile. 'RMS', 'COM' or 'RMSCOM' (Average of both)
        Returns: 
            List of the full widths half maximum for each bunch.
        """
        if not self._pulse_characterization:
            warnings.warn_explicit('Pulse characterization not created for current event due to issues with image. ' +\
                'Cannot construct pulse FWHM',UserWarning,'XTCAV',0)
            return None
            
        num_bunches = self._pulse_characterization.num_bunches
        if num_bunches < 1:
            return np.zeros((num_bunches), dtype=np.float64)
        
                  
        peakwidth=np.zeros((num_bunches), dtype=np.float64);
        for j in range(num_bunches):
            t = self._pulse_characterization.t + self._pulse_characterization.bunchdelay[j]
            if method == 'RMS':
                power = self._pulse_characterization.powerERMS[j]
            elif method=='COM':
                power = self._pulse_characterization.powerECOM[j]
            elif method=='RMSCOM':
                power = (self._pulse_characterization.powerECOM[j] + self._pulse_characterization.powerERMS[j])/2
            else:
                warnings.warn_explicit('Method %s not supported' % (method),UserWarning,'XTCAV',0)
                return None   
            #quadratic fit around 5 pixels method
            threshold=np.max(power)/2
            abovethrestimes=t[power>=threshold]
            dt=t[1]-t[0]
            peakwidth[j]=abovethrestimes[-1]-abovethrestimes[0]+dt
            
        return peakwidth
      
    def InterBunchPulseDelayBasedOnCurrent(self):    
        """
        Method which returns the time of lasing for each bunch based on the peak electron current on each bunch. A lasing off reference is not necessary for this retrieval. The delays are referred to the center of mass of the total current. The order of the delays goes from higher to lower energy electron bunches.

        Returns: 
            List with the delay for each bunch.
        """
        if not self._image_profile:
            warnings.warn_explicit('Image profile not created for current event due to issues with image. ' +\
                'Cannot construct inter bunch pulse delay',UserWarning,'XTCAV',0)
            return None
            
        # if (self._eventresultsstep1['NB']<1):
        #     return np.zeros((self._eventresultsstep1['NB']), dtype=np.float64)
        
        t = self._image_profile.physical_units.xfs   
          
        peakpos=np.zeros((self.num_bunches), dtype=np.float64);
        for j in range(0,self.num_bunches):
            #highest value method
            #peakpos[j]=t[np.argmax(self._eventresultsstep1['imageStats'][j]['xProfile'])]
            
            #five highest values method
            #ind=np.mean(np.argpartition(-self._eventresultsstep2['imageStats'][j]['xProfile'],5)[0:5]) #Find the position of the 5 highest values
            #peakpos[j]=t[ind]
            
            #quadratic fit around 5 pixels method
            central=np.argmax(self._image_profile.image_stats[j].xProfile)
            try:
                fit=np.polyfit(t[central-2:central+3], self._pulse_characterization.image_stats[j].xProfile[central-2:central+3],2)
                peakpos[j]=-fit[1]/(2*fit[0])
            except:
                return None 
            
        return peakpos
        
    def InterBunchPulseDelayBasedOnCurrentMultiple(self, n=1, filterwith=7):    
        """
        Method which returns multiple possible times of lasing for each bunch based on the peak electron current on each bunch. A lasing off reference is not necessary for this retrieval. The delays are referred to the center of mass of the total current. The order of the delays goes from higher to lower energy electron bunches. Then within each bunch the "n" delays are orderer from highest peak current yo lowest peak current.
        Args:
            n (int): number of possible times of lasing (peaks in the electron current) to find per bunch
            filterwith (float): Witdh of the peak that is removed before searching for the next peak in the same bunch
        Returns: 
            List with a list of "n" delays for each bunch.
        """
        if not self._image_profile:
            warnings.warn_explicit('Image profile not created for current event due to issues with image. ' +\
                'Cannot construct inter bunch pulse delay',UserWarning,'XTCAV',0)
            return None
        
        t = self._image_profile.physical_units.xfs  
          
        peakpos=np.zeros((self.num_bunches,n), dtype=np.float64);
           
        for j in range(0,self.num_bunches):
            profile = self._image_profile.image_stats[j].xProfile.copy()
            for k in range(n):
                #highest value method
                #peakpos[j]=t[np.argmax(self._eventresultsstep1['imageStats'][j]['xProfile'])]
                
                #five highest values method
                #ind=np.mean(np.argpartition(-self._eventresultsstep2['imageStats'][j]['xProfile'],5)[0:5]) #Find the position of the 5 highest values
                #peakpos[j]=t[ind]
                
                #quadratic fit around 5 pixels method
                central = np.argmax(profile)
                try:
                    fit = np.polyfit(t[central-2:central+3],profile[central-2:central+3],2)
                    peakpos[j,k] =- fit[1]/(2*fit[0])
                    filter = 1-np.exp(-(t-peakpos[j,k])**2/(filterwith/(2*np.sqrt(np.log(2))))**2)
                    profile = profile*filter                   
                except:
                    peakpos[j,k] = np.nan
                    if k==0:
                        return None
                
        return peakpos
        
    def InterBunchPulseDelayBasedOnCurrentFourierFiltered(self,targetwidthfs=20,thresholdfactor=0):    
        """
        Method which returns the time delay between the x-rays generated from different bunches based on the peak electron current on each bunch. A lasing off reference is not necessary for this retrieval. The delays are referred to the center of mass of the total current. The order of the delays goes from higher to lower energy electron bunches. This method includes a Fourier filter that applies a low pass filter to amplify the feature identified as the lasing part of the bunch, and ignore other peaks that may be higher in amplitude but also higher in width. It is possible to threshold the signal before calculating the Fourier transform to automatically discard peaks that may be sharp, but too low in amplitude to be the right peaks.
        Args:
            targetwidthfs (float): Witdh of the peak to be used for calculating delay
            thresholdfactor (float): Value between 0 and 1 that indicates which threshold factor to apply to filter the signal before calculating the fourier transform
        Returns: 
            List with the delay for each bunch.
        """
        if not self._image_profile:
            warnings.warn_explicit('Image profile not created for current event due to issues with image. ' +\
                'Cannot construct inter bunch pulse delay',UserWarning,'XTCAV',0)
            return None
             
        t = self._image_profile.physical_units.xfs    
        
        #Preparing the low pass filter
        N = len(t)
        dt = abs(self._image_profile.physical_units.xfsPerPix)
        if dt*N==0:
            return None
        df = 1./(dt*N)
        
        f = np.array(range(0, N/2+1) + range(-N/2+1,0))*df
                           
        ffilter=(1-np.exp(-(f*targetwidthfs)**6))
          
        peakpos=np.zeros((self.num_bunches), dtype=np.float64);
        for j in range(0,self.num_bunches):
            #Getting the profile and the filtered version
            profile = self._image_profile.image_stats[j].xProfile
            profilef = profile-np.max(profile)*thresholdfactor
            profilef[profilef<0] = 0
            profilef = np.fft.ifft(np.fft.fft(profilef)*ffilter)
        
            #highest value method
            #peakpos[j]=t[np.argmax(profilef)]
            
            #five highest values method
            #ind=np.mean(np.argpartition(-profilef,5)[0:5]) #Find the position of the 5 highest values
            #peakpos[j]=t[ind]
            
            #quadratic fit around 5 pixels method and then fit to the original signal
            central=np.argmax(profilef)
            try:
                fit=np.polyfit(t[central-2:central+3],profile[central-2:central+3],2)
                peakpos[j]=-fit[1]/(2*fit[0])
            except:
                return None 
            
        return peakpos

    def QuadRefine(self,p):
        x1,x2,x3 = p + np.array([-1,0,1])
        y1,y2,y3 = self.wf[(p-self.rangelim[0]-1):(p-self.rangelim[0]+2)]
        d = (x1-x2)*(x1-x3)*(x2-x3)
        A = ( x3 * (y2-y1) + x2 * (y1-y3) + x1 * (y3-y2) ) / d
        B = ( x3**2.0 * (y1-y2) + x2**2.0 * (y3-y1) + x1**2.0 * (y2-y3) ) / d
        return -1*B / (2*A)

    def ElectronCurrentPerBunch(self):    
        """
        Method which returns the electron current per bunch. A lasing off reference is not necessary for this retrieval.

        Returns: 
            out1: time vectors in fs
            out2: electron currents in arbitrary units
        """
        if not self._image_profile:
            warnings.warn_explicit('Image profile not created for current event due to issues with image. ' +\
                'Cannot construct electron current',UserWarning,'XTCAV',0)
            return None, None
        
        t = self._image_profile.physical_units.xfs    

        tout = np.zeros((self.num_bunches, len(t)), dtype=np.float64);
        currents = np.zeros((self.num_bunches, len(t)), dtype=np.float64);
        for j in range(0,self.num_bunches):
            tout[j,:]=t
            currents[j,:]=self._image_profile.image_stats[j].xProfile
                    
        return tout, currents
        

    def XRayPower(self, method='RMSCOM'):       
        """
        Method which returns the power profile for the X-Rays generated by each electron bunch. This is the averaged result from the RMS method and the COM method.

        Args:
            method (str): method to use to obtain the power profile. 'RMS', 'COM' or 'RMSCOM' (Average of both)
        Returns: 
            out1: time vectors in fs. 2D array where the first index refers to bunch number, and the second index to time.
            out2: power profiles in GW. 2D array where the first index refers to bunch number, and the second index to the power profile.
        """

        if not self._pulse_characterization:
            warnings.warn_explicit('Pulse characterization not created for current event due to issues with image. ' +\
                'Cannot construct pulse FWHM',UserWarning,'XTCAV',0)
            return None, None
                        
        mastert = self._pulse_characterization.t

        t = np.zeros((self.num_bunches, len(mastert)), dtype=np.float64);
        for j in range(self.num_bunches):
            t[j,:] = mastert+self._pulse_characterization.bunchdelay[j]

        if method=='RMS':
            power = self._pulse_characterization.powerERMS
        elif method=='COM':
            power = self._pulse_characterization.powerECOM
        elif method=='RMSCOM':
            power = (self._pulse_characterization.powerECOM + self._pulse_characterization.powerERMS)/2
        else:
            warnings.warn_explicit('Method %s not supported' % (method),UserWarning,'XTCAV',0)
            return t, None
            
        return t,power       
        
        
    def XRayEnergyPerBunch(self,method='RMSCOM'):   
        """
        Method which returns the total X-Ray energy generated per bunch. This is the averaged result from the RMS method and the COM method.
        Args:
            method (str): method to use to obtain the power profile. 'RMS', 'COM' or 'RMSCOM' (Average of both)
        Returns: 
            List with the values of the energy for each bunch in J
        """ 
        if not self._pulse_characterization:
            warnings.warn_explicit('Pulse characterization not created for current event due to issues with image. ' +\
                'Cannot construct pulse FWHM',UserWarning,'XTCAV',0)
            return None
        
        if method=='RMS':
            energyperbunch = self._pulse_characterization.lasingenergyperbunchERMS
        elif method=='COM':
            energyperbunch = self._pulse_characterization.lasingenergyperbunchECOM
        elif method=='RMSCOM':
            energyperbunch = (self._pulse_characterization.lasingenergyperbunchECOM + self._pulse_characterization.lasingenergyperbunchERMS)/2
        else:
            warnings.warn_explicit('Method %s not supported' % (method),UserWarning,'XTCAV',0)
            return None
       
        return energyperbunch  
        
    
    def ProcessedXTCAVImage(self):    
        """
        Method which returns the processed XTCAV image after background subtraction, noise removal, region of interest cropping and multiple bunch separation. This does not require a lasing off reference.

        Returns: 
            3D array where the first index is bunch number, and the other two are the image.
        """     
        if self._processed_image is None:
            warnings.warn_explicit('Image not processed for current event due to issues with image. ' +\
                'Returning raw image',UserWarning,'XTCAV',0)
            return self._rawimage
          
        return self._processed_image
        
    def ProcessedXTCAVImageROI(self):    
        """
        Method which returns the position of the processed XTCAV image within the whole CCD after background subtraction, noise removal, region of interest cropping and multiple bunch separation. This does not require a lasing off reference.

        Returns: 
            Dictionary with the region of interest parameters.
        """     
        if self._processed_image is None:
            warnings.warn_explicit('Image profile not created for current event due to issues with image.',UserWarning,'XTCAV',0)
            return None
            
        return self._image_profile.roi
        
    def ReconstructionAgreement(self): 
        """
        Value for the agreement of the reconstruction using the RMS method and using the COM method. It consists of a value ranging from -1 to 1.

        Returns: 
            value for the agreement.
        """
        if not self._pulse_characterization:
            warnings.warn_explicit('Pulse characterization not created for current event due to issues with image. ' +\
                'Cannot calculate reconstruction agreement',UserWarning,'XTCAV',0)
            return 0
                       
        return np.mean(self._pulse_characterization.powerAgreement)     
        
