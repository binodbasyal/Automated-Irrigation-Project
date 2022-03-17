#!/usr/local/opt/python-3.5.1/bin/python3.5
# 2022-01-19 Added GPIO feature for irrigation
# 2021-10-25 Updated to up to 8 fields for thingspeak and updated API address. Also removed curl and used request
# SDI-12 Sensor Data Logger Copyright Dr. John Liu 2017-11-06
# 2017-11-06 Updated telemetry code to upload to thingspeak.com from data.sparkfun.com.
# 2017-06-23 Added exception handling in case the SDI-12 + GPS USB adapter doesn't return any data (no GPS lock).
#            Added serial port and file closing in ctrl + C handler.
# 2017-02-02 Added multiple-sensor support. Just type in multiple sensor addresses when asked for addresses.
#            Changed sdi_12_address into regular string from byte string. I found out that byte strings when iterated over becomes integers.
#            It's easy to cast each single character string into byte string with .encode() when needed as address.
#            Removed specific analog input code and added the adapter address to the address string instead.
# 2016-11-12 Added support for analog inputs
# 2016-07-01 Added .strip() to remove \r from input files typed in windows
# Added Ctrl-C handler
# Added sort of serial port placing FTDI at item 0 if it exists

# Credentials

min_VWC_percent=20

import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BOARD)

import datetime # For finding system's real time
import json
import os # For running command line commands
import platform # For detecting operating system flavor
import re # For regular expression support
import serial.tools.list_ports # For listing available serial ports
import serial # For serial communication
import signal # For trapping ctrl-c or SIGINT
import sys # For exiting program with exit code
import time # For delaying in seconds
import urllib.parse # For encoding data to be url safe.
import urllib.request  # send data to online server
import requests # sends data to cloud. To install this module, in RPI, open a terminal window, then type sudo pip3 install requests
import shutil

src = '/home/pi/Desktop/python_codes_for_irrigation_project'
dst = '/mnt/automaticirrigation'


def SIGINT_handler(signal, frame):
    ser.close()
    data_file.close()
    print('Quitting program!')
    sys.exit(0)
signal.signal(signal.SIGINT, SIGINT_handler)

def TER12_VWC_percentage_Soilless(RAW):
    return 100*(6.771e-10*RAW**3-5.105e-6*RAW**2+1.302e-2*RAW-10.848)

def TER12_VWC_percentage_Custom(RAW):
    return round(((-0.0018*RAW)+35.619)*100,1)

unit_id=platform.node() # Use computer name as unit_id. For a raspberry pi, change its name from raspberrypi to something else to avoid confusion
adapter_sdi_12_address='z'
no_data=False # This is the flag to break out of the inner loops and continue the next data point loop in case no data is received from a sensor such as the GPS.

print('+-'*40)
print('SDI-12 Sensor and Analog Sensor Python Data Logger with irrigation control and Telemetry V1.5.0')
print('Designed for Dr. Liu\'s family of SDI-12 USB adapters (standard,analog,GPS)\n\tDr. John Liu Saint Cloud MN USA 2022-01-19\n')
print('\nCompatible with Windows, GNU/Linux, Mac OSX, and Raspberry PI')
print('\nThis program requires Python 3.4, Pyserial 3.0, requests and urllib (data upload)')
print('\nData is logged to YYYYMMDD.CVS in the Python code\'s folder')

print ('\nFor assistance with customization, telemetry etc., contact Dr. Liu.\n\thttps://liudr.wordpress.com/gadget/sdi-12-usb-adapter/')
print('+-'*40)

ports=[]
VID_FTDI=0x0403;

a=serial.tools.list_ports.comports()
for w in a:
    ports.append((w.vid,w.device))

ports.sort(key= lambda ports: ports[1])

print('\nDetected the following serial ports:')
i=0
for w in ports:
    print('%d)\t%s\t(USB VID=%04X)' %(i, w[1], w[0] if (type(w[0]) is int) else 0))
    i=i+1
total_ports=i # now i= total ports

user_port_selection=input('\nSelect port from list (0,1,2...). SDI-12 adapter has USB VID=0403:')
if (int(user_port_selection)>=total_ports):
    exit(1) # port selection out of range

ser=serial.Serial(port=(ports[int(user_port_selection)])[1],baudrate=9600,timeout=10)
time.sleep(2.5) # delay for arduino bootloader and the 1 second delay of the adapter.

total_data_count=int(input('Total number of data points:'))
delay_between_pts=int(input('Delay between data points (second):'))

print('Time stamps are generated with:\n0) GMT/UTC\n1) Local\n')
time_zone_choice=int(input('Select time zone.'))

   
sdi_12_address=''
relay_GPIO={}   # This is the dictionary of the GPIO controlling the relay for the corresponding sensor' pot, such as {'1':18,'2':19}
user_sdi_12_address=input('Enter all SDI-12 sensor addresses, such as 1,2,3,4,5,6,7,8:')
user_GPIO_pins=input('Enter all RPI GPIO BOARD pins controlling relays in the order of the sensors, such as 7,11,12,13,15,16,18,22:')
relay_GPIO=dict(zip(user_sdi_12_address.split(','),list(map(lambda x:int(x),user_GPIO_pins.split(',')))))
user_sdi_12_address=user_sdi_12_address.strip() # Remove any \r from an input file typed in windows

for an_address in sorted(relay_GPIO.keys()):
    print(an_address)
    ser.write(an_address.encode()+b'I!')
    sdi_12_line=ser.readline()
    print(sdi_12_line)
    if ((an_address>='0') and (an_address<='9')) or ((an_address>='A') and (an_address<='Z')) or ((an_address>='a') and (an_address<='z')):
        GPIO.setup(relay_GPIO[an_address],GPIO.OUT)
        GPIO.output(relay_GPIO[an_address],GPIO.LOW)
        print("Sensor address: %s Sensor info: %s --> Relay GPIO: %2d" %(an_address,sdi_12_line.decode('utf-8').strip()[3:], relay_GPIO[an_address]))
        sdi_12_address=sdi_12_address+an_address
    else:
        print('Invalid address:',an_address)
print()

for j in range(total_data_count):
    thingspeak_values_str='' # This stores &value0=xxx&value1=xxx&value2=xxx&value3=xxx&value4=xxx&value5=xxx and is only reset after all sensors are read.
    if time_zone_choice==0:
        now=datetime.datetime.utcnow()
    elif time_zone_choice==1:
        now=datetime.datetime.now()
    tstamp=int(now.timestamp())  # Timestamp in the request must be integer.
    TER12Calcs=[]   # Contains calculated values of all TER12 sensors.
    thingspeak_values=[]
    grafana_json_data=[]
    for an_address in sorted(relay_GPIO.keys()):
        ser.write(an_address.encode()+b'M!'); # start the SDI-12 sensor measurement
        # print(an_address.encode()+b'M!'); # start the SDI-12 sensor measurement
        sdi_12_line=ser.readline()
        # print(sdi_12_line)
        sdi_12_line=sdi_12_line[:-2] # remove \r and \n since [0-9]$ has trouble with \r
        m=re.search(b'[0-9]$',sdi_12_line) # having trouble with the \r
        total_returned_values=int(m.group(0)) # find how many values are returned
        sdi_12_line=ser.readline() # read the service request line
        ser.write(an_address.encode()+b'D0!') # request data
        # print(an_address.encode()+b'D0!') # request data
        sdi_12_line=ser.readline() # read the data line
        # print(sdi_12_line)
        sdi_12_line=sdi_12_line[1:-2] # remove address, \r and \n since [0-9]$ has trouble with \r

        TER12Values=[] # Contains raw values from one sensor we're reading. Clear before each sensor
        for iterator in range(total_returned_values): # extract the returned values from SDI-12 sensor and append to values[]
            m=re.search(b'[+-][0-9.]+',sdi_12_line) # match a number string
            try: # if values found is less than values indicated by return from M, report no data found. This is a simple solution to GPS sensors before they acquire lock. For sensors that have lots of values to return, you need to find a better solution.
                TER12Values.append(float(m.group(0))) # convert into a number
                sdi_12_line=sdi_12_line[len(m.group(0)):]
            except AttributeError:
                print("No data received from sensor at address %c\n" %(an_address))
                time.sleep(delay_between_pts)
                no_data=True
                break
        if (no_data==True):
            break;
        wc=TER12Values[0]
        tempC=TER12Values[1]
        ec=TER12Values[2]
        VWC_percent_custom=TER12_VWC_percentage_Custom(wc)

        TER12Calcs.append([tempC,VWC_percent_custom])
        thingspeak_values.append(VWC_percent_custom)

        # Automatically irrigate pot
        if (VWC_percent_custom<min_VWC_percent):
            print("Too Dry, Irrigation Started! VWC values(percentage): Sensor(TEROS12):%s" %(VWC_percent_custom))
            irrigation_counts=1
            GPIO.output(relay_GPIO[an_address],GPIO.HIGH)
            time.slee
            p(10)
            GPIO.output(relay_GPIO[an_address], GPIO.LOW)
        else:
            print('Do Not Need Irrigation. VWC values: Sensor(TEROS12): %s' %(VWC_percent_custom))
            irrigation_counts=0
            GPIO.output(relay_GPIO[an_address], GPIO.LOW)
    if (no_data==True):
        no_data=False
        continue
    else:
        # Format output file
        file_output_str="%04d/%02d/%02d %02d:%02d:%02d%s,%d" %(now.year,now.month,now.day,now.hour,now.minute,now.second,' GMT' if time_zone_choice==0 else '',tstamp) # formatting date and time
        for oneTER12Calcs in TER12Calcs:
            file_output_str=file_output_str+",%s,%s" %(oneTER12Calcs[0],oneTER12Calcs[1])
        file_output_str=file_output_str+'\n'
        data_file_name="%04d%02d%02d.csv" %(now.year,now.month,now.day)
        print('Saving to %s\n%s' %(data_file_name,file_output_str))
        data_file = open(data_file_name, 'a') # open yyyymmdd.csv for appending
        data_file.write(file_output_str)
        data_file.close()
        shutil.move((src+'/'+data_file_name),(dst+'/'+data_file_name))
        

        print('+-'*40)
    afterposting=datetime.datetime.now()
    time.sleep(delay_between_pts-(afterposting-now).seconds)
ser.close()