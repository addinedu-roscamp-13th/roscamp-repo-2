from setuptools import find_packages, setup

package_name = 'pinky_buzzer'

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
    description='부저 ROS2 서비스 노드',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'buzzer_node=pinky_buzzer.buzzer_node:main',
        ],
    },
)
