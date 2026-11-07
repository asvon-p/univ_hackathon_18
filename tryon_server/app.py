"""Gradio 웹 데모: FASHN VTON v1.5 로컬(GPU) 가상 시착.

실행:  python app.py   (기본 http://127.0.0.1:7860)
"""
from __future__ import annotations

import os

import gradio as gr

import local_tryon as lt

CATEGORY_LABELS = {
    "tops": "상의 (tops)",
    "bottoms": "하의 (bottoms)",
    "one-pieces": "원피스 (one-pieces)",
}
LABEL_TO_CATEGORY = {v: k for k, v in CATEGORY_LABELS.items()}
PHOTO_TYPE_LABELS = {"auto": "자동 판별 (auto)", "model": "착용 사진 (model)", "flat-lay": "누끼/평면 상품 사진 (flat-lay)"}
LABEL_TO_PHOTO_TYPE = {v: k for k, v in PHOTO_TYPE_LABELS.items()}


def do_tryon(person, garment, category_label, photo_type_label, num_samples, num_timesteps,
             guidance_scale, seed, segmentation_free):
    if person is None or garment is None:
        raise gr.Error("전신 사진과 의상 사진을 모두 올려주세요.")
    try:
        res = lt.run_tryon(
            person, garment,
            category=LABEL_TO_CATEGORY[category_label],
            garment_photo_type=LABEL_TO_PHOTO_TYPE[photo_type_label],
            num_samples=num_samples,
            num_timesteps=num_timesteps,
            guidance_scale=guidance_scale,
            seed=seed,
            segmentation_free=segmentation_free,
            save_prefix="web",
        )
    except FileNotFoundError as e:
        raise gr.Error(str(e))
    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            raise gr.Error("CUDA 메모리 부족: 생성 장수를 1로 줄이거나 다른 GPU 프로그램을 종료하세요.")
        raise gr.Error(str(e)[:500])
    peak = f"{res.peak_vram_gb:.2f}GB" if res.peak_vram_gb is not None else "n/a"
    log = (f"완료: {len(res.images)}장 / {res.elapsed_sec:.1f}초 / 최대 VRAM {peak} / "
           f"옷 사진 종류={res.garment_photo_type}\n"
           + "\n".join(f"저장: {p}" for p in res.saved))
    return res.images, log


with gr.Blocks(title="FASHN VTON v1.5 로컬 파일럿") as demo:
    gr.Markdown(
        "## 가상 시착 파일럿 — FASHN VTON v1.5 (로컬 GPU)\n"
        f"환경: `{lt.gpu_info()}`  \n"
        "전신 사진과 의상 사진을 올리고 카테고리를 고른 뒤 **시착 실행**. 첫 실행은 모델 로드로 수십 초 걸림. "
        "결과는 `outputs/` 에도 저장됨."
    )
    with gr.Row():
        person = gr.Image(label="전신 사진 (사람)", type="pil", height=420)
        garment = gr.Image(label="의상 사진", type="pil", height=420)
        gallery = gr.Gallery(label="시착 결과 (576×864)", columns=2, height=420)
    with gr.Row():
        category = gr.Dropdown(choices=list(CATEGORY_LABELS.values()), value=CATEGORY_LABELS["tops"], label="의상 카테고리")
        photo_type = gr.Dropdown(choices=list(PHOTO_TYPE_LABELS.values()), value=PHOTO_TYPE_LABELS["auto"], label="의상 사진 종류")
        num_samples = gr.Slider(1, 4, value=1, step=1, label="생성 장수 (VRAM 8GB 면 1~2 권장)")
    with gr.Accordion("고급 옵션", open=False):
        with gr.Row():
            num_timesteps = gr.Slider(10, 50, value=30, step=1, label="샘플링 스텝 (20 빠름 / 30 보통 / 50 고품질)")
            guidance_scale = gr.Slider(1.0, 3.0, value=1.5, step=0.1, label="guidance_scale")
            seed = gr.Number(value=42, precision=0, label="seed")
            segmentation_free = gr.Checkbox(value=True, label="segmentation_free (권장: 켬)")
    run_btn = gr.Button("시착 실행", variant="primary")
    log_box = gr.Textbox(label="로그", lines=4)

    run_btn.click(
        do_tryon,
        inputs=[person, garment, category, photo_type, num_samples, num_timesteps, guidance_scale, seed, segmentation_free],
        outputs=[gallery, log_box],
    )

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=int(os.getenv("PORT", "7860")))
