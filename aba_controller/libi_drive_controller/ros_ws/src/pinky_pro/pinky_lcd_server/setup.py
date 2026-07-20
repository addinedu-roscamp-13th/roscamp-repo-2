from setuptools import find_packages, setup

package_name = 'pinky_lcd_server'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='roscamp',
    maintainer_email='propose101@gmail.com',
    description='LCD 텍스트 ROS2 서비스 노드',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'lcd_node=pinky_lcd_server.lcd_node:main',
        ],
    },
)
