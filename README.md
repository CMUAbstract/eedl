# <span style = "text-decoration:underline">EEDL: Earth Engine Downloader</span>

Python script for downloading Landsat 8, Landsat 9, and Sentinel 2 imagery from Google Earth Engine.
General options:
  * Directly download raw Landsat imagery directly
  * Directly download random Sentinel 2 mosaics in the shape of raw Landsat imagery
  * Download random Landsat mosaics with designated size to Google Drive
  * Download random Sentinel mosaics with designated size to Google Drive
A more detailed explanation of options is available in [Usage](https://github.com/CMUAbstract/eedl/edit/main/README.md#usage).

## Set-up
In a conda environment perform the following commands:
```
conda create -n eedl
conda activate eedl
conda install -c conda-forge earthengine-api
```

## Usage

Available commands:
* ```-b, --bounds```: Specific latitude and longitude bounds to define region of interest for downloads. Defaults to region centered on Florida, USA.
* ```-g, --grid_key```: Military Grid Reference System (MGRS) designated region describing a section of the Earth. Examples: 17R, 32S.
                        Overrides ```--bounds``` argument.
* ```-i, --idate```: Initial date for 
