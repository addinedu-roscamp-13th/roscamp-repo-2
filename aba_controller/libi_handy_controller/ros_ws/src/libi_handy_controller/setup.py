from setuptools import find_packages, setup

package_name = "libi_handy_controller"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="leekt",
    maintainer_email="dlrkdxor0821@gmail.com",
    description="LIBI 로봇팔(Handy) 컨트롤러 — pick/place 요청 수신, 팔 동작(스텁), 결과 발행",
    license="TODO",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "handy_node=libi_handy_controller.handy_node:main",
        ],
    },
)
