-- Synthetic test fixture (handoff A29): one SSAS processing job, scripted the way
-- SSMS "Script Job as > CREATE To" writes it. It names no real system.
USE [msdb]
GO
EXEC msdb.dbo.sp_add_job
    @job_name = N'Seeded Nightly Processing',
    @enabled = 1,
    @description = N'Full process of the seeded sales cube.',
    @owner_login_name = N'sa'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Nightly Processing',
    @step_id = 1,
    @step_name = N'Process the sales cube',
    @subsystem = N'ANALYSISCOMMAND',
    @server = N'SEEDED-SSAS-01',
    @command = N'<Batch Transaction="true" xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">
  <Process>
    <Type>ProcessFull</Type>
    <Object>
      <DatabaseID>SeededDW</DatabaseID>
      <CubeID>Sales</CubeID>
    </Object>
  </Process>
</Batch>'
EXEC msdb.dbo.sp_add_jobschedule
    @job_name = N'Seeded Nightly Processing',
    @name = N'Nightly 23:00',
    @enabled = 1,
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 1,
    @active_start_time = 230000
EXEC msdb.dbo.sp_add_jobserver @job_name = N'Seeded Nightly Processing'
GO
