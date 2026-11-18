-- Synthetic example: an SSAS processing job and a T-SQL cleanup job.
-- Written for the sqlagent-doc-gen test fixtures; contains no real system
-- names, credentials, or customer data.

USE [msdb]
GO

IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs_view WHERE name = N'SSAS Nightly Processing')
EXEC msdb.dbo.sp_delete_job @job_name = N'SSAS Nightly Processing', @delete_unused_schedule = 1
GO

BEGIN TRANSACTION
DECLARE @ReturnCode INT
DECLARE @jobId BINARY(16)

EXEC @ReturnCode = msdb.dbo.sp_add_job
    @job_name = N'SSAS Nightly Processing',
    @enabled = 1,
    @description = N'Full process of the sales cube, then dimensions.',
    @category_name = N'[Uncategorized (Local)]',
    @owner_login_name = N'sa',
    @notify_level_eventlog = 2,
    @notify_level_email = 0,
    @delete_level = 0,
    @job_id = @jobId OUTPUT

IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback

EXEC @ReturnCode = msdb.dbo.sp_add_jobstep
    @job_id = @jobId,
    @step_name = N'Process Sales cube',
    @subsystem = N'ANALYSISCOMMAND',
    @server = N'SSAS-EXAMPLE-01',
    @command = N'<Batch Transaction="true" xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">
  <Process>
    <Type>ProcessFull</Type>
    <Object>
      <DatabaseID>SalesDW</DatabaseID>
      <CubeID>Sales</CubeID>
    </Object>
  </Process>
</Batch>',
    @retry_attempts = 3,
    @retry_interval = 15,
    @on_fail_action = 2

EXEC @ReturnCode = msdb.dbo.sp_add_jobstep
    @job_id = @jobId,
    @step_name = N'Refresh tabular partitions',
    @subsystem = N'ANALYSISCOMMAND',
    @server = N'SSAS-EXAMPLE-01',
    @command = N'{"refresh": {"type": "add", "objects": [{"database": "SalesTabular", "table": "FactSales", "partition": "FactSales-Current"}]}}',
    @proxy_name = N'EXAMPLE\svc_proxy'

EXEC @ReturnCode = msdb.dbo.sp_add_jobschedule
    @job_id = @jobId,
    @name = N'Weeknights 22:00',
    @enabled = 1,
    @freq_type = 8,
    @freq_interval = 62,
    @freq_subday_type = 1,
    @active_start_time = 220000,
    @active_start_date = 20260101

EXEC @ReturnCode = msdb.dbo.sp_add_jobserver @job_id = @jobId, @server_name = N'(local)'

COMMIT TRANSACTION
GOTO EndSave
QuitWithRollback:
    IF (@@TRANCOUNT > 0) ROLLBACK TRANSACTION
EndSave:
GO

-- A plain T-SQL job sharing a schedule via sp_add_schedule / sp_attach_schedule.
BEGIN TRANSACTION
DECLARE @ReturnCode2 INT
DECLARE @jobId2 BINARY(16)
DECLARE @sharedScheduleId INT

EXEC @ReturnCode2 = msdb.dbo.sp_add_job
    @job_name = N'Warehouse Cleanup',
    @enabled = 1,
    @owner_login_name = N'EXAMPLE\svc_sqlagent',
    @notify_level_email = 2,
    @notify_email_operator_name = N'EXAMPLE DBA On-Call',
    @job_id = @jobId2 OUTPUT

EXEC @ReturnCode2 = msdb.dbo.sp_add_jobstep
    @job_id = @jobId2,
    @step_name = N'Delete staging rows',
    @subsystem = N'TSQL',
    @database_name = N'WarehouseStaging',
    @command = N'DELETE FROM staging.LoadLog WHERE LoadDate < DATEADD(day, -30, SYSDATETIME());
EXEC staging.usp_PurgeErrors @OlderThanDays = 30;'

EXEC @ReturnCode2 = msdb.dbo.sp_add_schedule
    @schedule_name = N'Shared Weekly Maintenance',
    @enabled = 1,
    @freq_type = 8,
    @freq_interval = 1,
    @freq_subday_type = 1,
    @active_start_time = 30000,
    @schedule_id = @sharedScheduleId OUTPUT

EXEC @ReturnCode2 = msdb.dbo.sp_attach_schedule
    @job_id = @jobId2, @schedule_id = @sharedScheduleId

EXEC @ReturnCode2 = msdb.dbo.sp_add_jobserver @job_id = @jobId2
COMMIT TRANSACTION
GO
