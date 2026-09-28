---
language:
- en
tags:
- Manga
- Object Detection
- OCR
- Clustering
- Diarisation
---
<style>
  .title-container {
    display: flex;
    flex-direction: column; /* Stack elements vertically */
    justify-content: center;
    align-items: center;
  }
  
  .title {
    font-size: 2em;
    text-align: center;
    color: #333;
    font-family: 'Comic Sans MS', cursive; /* Use Comic Sans MS font */
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 0.5em 0 0.2em;
    background: transparent;
  }
  
  .title span {
    background: -webkit-linear-gradient(45deg, #6495ED, #4169E1); /* Blue gradient */
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .subheading {
    font-size: 1.5em; /* Adjust the size as needed */
    text-align: center;
    color: #555; /* Adjust the color as needed */
    font-family: 'Comic Sans MS', cursive; /* Use Comic Sans MS font */
  }

  .authors {
    font-size: 1em; /* Adjust the size as needed */
    text-align: center;
    color: #777; /* Adjust the color as needed */
    font-family: 'Comic Sans MS', cursive; /* Use Comic Sans MS font */
    padding-top: 1em;
  }

  .affil {
    font-size: 1em; /* Adjust the size as needed */
    text-align: center;
    color: #777; /* Adjust the color as needed */
    font-family: 'Comic Sans MS', cursive; /* Use Comic Sans MS font */
  }

</style>

<div class="title-container">
  <div class="title">
    Ta<span>il</span>s Tell Ta<span>le</span>s
  </div>
  <div class="subheading">
    Chapter-Wide Manga Transcriptions With Character Names
  </div>
  <div class="authors">
    Ragav Sachdeva, Gyungin Shin and Andrew Zisserman
  </div>
  <div class="affil">
    University of Oxford
  </div>
  <div style="display: flex;">
    <a href="https://arxiv.org/abs/2408.00298"><img alt="Static Badge" src="https://img.shields.io/badge/arXiv-2408.00298-blue"></a>
    &emsp;
    <img alt="Dynamic JSON Badge" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fhuggingface.co%2Fapi%2Fmodels%2Fragavsachdeva%2Fmagiv2%3Fexpand%255B%255D%3Ddownloads%26expand%255B%255D%3DdownloadsAllTime&query=%24.downloadsAllTime&label=%F0%9F%A4%97%20Downloads">
  </div>
</div>


![image/png](https://cdn-uploads.huggingface.co/production/uploads/630852d2f0dc38fb47c347a4/OQW4r_A3aA9RrWpG6Wkve.png)

# Usage
```python
from PIL import Image
import numpy as np
from transformers import AutoModel
import torch

model = AutoModel.from_pretrained("ragavsachdeva/magiv2", trust_remote_code=True).cuda().eval()


def read_image(path_to_image):
    with open(path_to_image, "rb") as file:
        image = Image.open(file).convert("L").convert("RGB")
        image = np.array(image)
    return image

chapter_pages = ["page1.png", "page2.png", "page3.png" ...]
character_bank = {
    "images": ["char1.png", "char2.png", "char3.png", "char4.png" ...],
    "names": ["Luffy", "Sanji", "Zoro", "Ussop" ...]
}

chapter_pages = [read_image(x) for x in chapter_pages]
character_bank["images"] = [read_image(x) for x in character_bank["images"]]

with torch.no_grad():
    per_page_results = model.do_chapter_wide_prediction(chapter_pages, character_bank, use_tqdm=True, do_ocr=True)

transcript = []
for i, (image, page_result) in enumerate(zip(chapter_pages, per_page_results)):
    model.visualise_single_image_prediction(image, page_result, f"page_{i}.png")
    speaker_name = {
        text_idx: page_result["character_names"][char_idx] for text_idx, char_idx in page_result["text_character_associations"]
    }
    for j in range(len(page_result["ocr"])):
        if not page_result["is_essential_text"][j]:
            continue
        name = speaker_name.get(j, "unsure") 
        transcript.append(f"<{name}>: {page_result['ocr'][j]}")
with open(f"transcript.txt", "w") as fh:
    for line in transcript:
        fh.write(line + "\n")
```

# License and Citation
The provided model and datasets are available for unrestricted use in personal, research, non-commercial, and not-for-profit endeavors. For any other usage scenarios, kindly contact me via email, providing a detailed description of your requirements, to establish a tailored licensing arrangement.
My contact information can be found on my website: ragavsachdeva [dot] github [dot] io

```
@misc{magiv2,
      title={Tails Tell Tales: Chapter-Wide Manga Transcriptions with Character Names}, 
      author={Ragav Sachdeva and Gyungin Shin and Andrew Zisserman},
      year={2024},
      eprint={2408.00298},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2408.00298}, 
}
```