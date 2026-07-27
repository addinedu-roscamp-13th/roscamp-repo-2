from setuptools import find_packages, setup

package_name = 'libi_perception'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['tests']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'py_trees'],
    zip_safe=True,
    maintainer='leekt',
    maintainer_email='dlrkdxor0821@gmail.com',
    description='LIBI 사람 추종 (PID + LiDAR + 회복 BT).',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'follow_node = libi_perception.follow_node:main',
            # 통행 금지 마스크 발행. pi-all.sh --dyn-obstacle 로만 뜬다(기본 꺼짐).
            'keepout_node = libi_perception.keepout_mask:main',
        ],
    },
)
