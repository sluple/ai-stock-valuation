"""emissions_training_data.csv로 배출량 추정 모델을 학습하고 저장한다.

실행: python train_model.py
"""
import sys

from emissions_model import load_training_data, save_model, train_model

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    df = load_training_data()
    print(f"학습 데이터: {len(df)}개 기업")
    print(f"업종 수: {df['계획업종'].nunique()}개")

    artifact = train_model(df)
    m = artifact["metrics"]
    print(f"\n=== 모델 성능 ===")
    print(f"R2: {m['r2']:.3f}")
    print(f"학습표본: {m['n_train']}, 검증표본: {m['n_test']}")
    print(f"대략적인 배수 오차(로그MAE 환산): x{1 + m['approx_fold_error']:.2f}")

    save_model(artifact)
    print("\n모델 저장 완료 -> ../data/emissions_model.pkl")


if __name__ == "__main__":
    main()
