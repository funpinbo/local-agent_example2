# 메모리 부족(OOM) 대응

## 증상
요청이 몰릴 때 CUDA out of memory 오류로 요청이 실패하거나 서버가 내려간다.

## 주요 원인
- gpu-memory-utilization을 너무 높게 잡아 운영체제와 다른 프로세스 자리가 없음
- max-model-len이 커서 KV 캐시가 차지하는 메모리가 큼
- 동시 요청 수(max-num-seqs)가 많아 한꺼번에 잡히는 KV 캐시가 큼

## 대응
gpu-memory-utilization은 0.85부터 시작해 올린다. 긴 문서 요약이 필요 없는 모델은 max-model-len을 줄인다. 동시 요청 수는 부하 테스트 결과를 보고 정한다.
