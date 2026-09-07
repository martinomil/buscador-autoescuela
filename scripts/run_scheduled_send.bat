@echo off
REM Ejecutado por el Programador de tareas de Windows (ver README, seccion
REM "Envio programado"). Envia de verdad (send-batch --confirm) a todas las
REM autoescuelas pendientes, sin pedir confirmacion interactiva.
cd /d C:\permiso_b\buscador
echo ==== %date% %time% ==== >> logs\scheduled_send.log
".venv\Scripts\python.exe" -m app.cli send-batch --confirm >> logs\scheduled_send.log 2>&1
echo ==== fin ==== >> logs\scheduled_send.log
