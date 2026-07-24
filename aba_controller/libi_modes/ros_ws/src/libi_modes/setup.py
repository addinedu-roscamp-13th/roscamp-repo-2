from setuptools import find_packages, setup

package_name = 'libi_modes'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/params.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='leekt',
    maintainer_email='dlrkdxor0821@gmail.com',
    description='LIBI 미션 FSM+BT (py_trees)',
    license='Proprietary',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'fsm_node = libi_modes.main:main',
        ],
    },
)
