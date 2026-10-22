# Image credits

Test images used by `eval/run_image_eval.py`. All come from
[Wikimedia Commons](https://commons.wikimedia.org/) except the lab report, and keep
their original licences.

| File | Source | Author | Licence |
|---|---|---|---|
| `01_cxr_normal.jpg` | [Normal posteroanterior (PA) chest radiograph (X-ray).jpg](https://commons.wikimedia.org/wiki/File:Normal_posteroanterior_(PA)_chest_radiograph_(X-ray).jpg) | Mikael Häggström | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `02_cxr_pneumonia.jpg` | [X-ray of lobar pneumonia.jpg](https://commons.wikimedia.org/wiki/File:X-ray_of_lobar_pneumonia.jpg) | Mikael Häggström, M.D. | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `03_cxr_pneumothorax.jpg` | [Pneumothorax CXR.jpg](https://commons.wikimedia.org/wiki/File:Pneumothorax_CXR.jpg) | Clinical Cases (Wikimedia user) | [CC BY-SA 2.5](https://creativecommons.org/licenses/by-sa/2.5) |
| `05_fundus_diabetic_retinopathy.png` | [Fundus - diabetic retinopathy.png](https://commons.wikimedia.org/wiki/File:Fundus_-_diabetic_retinopathy.png) | Shaofeng Hao, Changyan Liu, Na Li, Yanrong Wu, Dongdong Li, Qingyue Gao, Ziyou Yuan, Guanyan Li, Huilin Li, Jianzhou Yang and Shengfu Fan | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0) |
| `06_derm_shingles.jpg` | [Shingles Rash (46560965991).jpg](https://commons.wikimedia.org/wiki/File:Shingles_Rash_(46560965991).jpg) | NIAID | [CC BY 2.0](https://creativecommons.org/licenses/by/2.0) |
| `07_histo_adenocarcinoma.jpg` | [Adenocarcinoma of the colon-histology.JPG](https://commons.wikimedia.org/wiki/File:Adenocarcinoma_of_the_colon-histology.JPG) | Netha Hussain | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0) |
| `08_ct_subdural_hematoma.png` | [Ct-scan of the brain with an subdural hematoma.jpg](https://commons.wikimedia.org/wiki/File:Ct-scan_of_the_brain_with_an_subdural_hematoma.jpg) | Lucien Monfils | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0) |
| `09_mri_glioblastoma.jpg` | [AFIP-00405589-Glioblastoma-Radiology.jpg](https://commons.wikimedia.org/wiki/File:AFIP-00405589-Glioblastoma-Radiology.jpg) | Armed Forces Institute of Pathology | Public domain |
| `10_lab_report.png` | Created for this project | — | Synthetic, not a real patient |

## Changes

- `08_ct_subdural_hematoma.png`: the red arrows of the original were removed digitally
  and the image was converted to greyscale PNG, so they don't give the answer away. This
  modified version is shared under the same licence, CC BY-SA 3.0.
- `01`, `02`, `06` and `07` were downscaled to a maximum of 1024 px (the app resizes every
  image to that size before sending it to the model anyway). Modified versions of CC BY-SA
  images are shared under the same licence.
- All files were renamed.

## Note on test 7

The source file is titled "Adenocarcinoma of the colon", but at this magnification it
looks like nearly normal colonic mucosa. Test 7 therefore checks that the model
recognises colonic tissue, not that it diagnoses cancer.
