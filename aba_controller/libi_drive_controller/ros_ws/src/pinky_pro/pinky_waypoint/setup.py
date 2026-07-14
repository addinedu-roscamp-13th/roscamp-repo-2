from setuptools import find_packages, setup
import os, glob

package_name = 'pinky_waypoint'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob.glob(os.path.join('launch', '*.launch.xml'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='pinklab',
    maintainer_email='kyung133851@pinklab.art',
    description='RViz 클릭으로 경유지/목적지를 받아 Nav2로 경유지 통과 주행',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            "waypoint_follower=pinky_waypoint.waypoint_follower:main",
        ],
    },
)
