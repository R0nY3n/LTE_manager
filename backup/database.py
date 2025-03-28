import sqlite3
import os
from datetime import datetime

class LTEDatabase:
    def __init__(self, db_path=None):
        """初始化数据库连接

        Args:
            db_path: 数据库文件路径，如未指定则使用用户目录下的.LTE/lte_data.db
        """
        if db_path is None:
            # 默认使用用户主目录下的.LTE文件夹
            user_home = os.path.expanduser('~')
            lte_dir = os.path.join(user_home, '.LTE')
            if not os.path.exists(lte_dir):
                os.makedirs(lte_dir)
            self.db_path = os.path.join(lte_dir, 'lte_data.db')
        else:
            self.db_path = db_path

        print(f"使用数据库: {self.db_path}")
        self.conn = None
        self.cursor = None
        self.connect()
        self.create_tables()

    def connect(self):
        """Connect to the database"""
        try:
            # Create database directory if it doesn't exist
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)

            # Connect to database
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            return True
        except Exception as e:
            print(f"Database connection error: {str(e)}")
            return False

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

    def create_tables(self):
        """Create necessary tables if they don't exist"""
        try:
            # Call history table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS call_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT NOT NULL,
                    call_type TEXT NOT NULL,
                    duration INTEGER DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    notes TEXT
                )
            ''')

            # SMS history table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS sms_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT NOT NULL,
                    message TEXT NOT NULL,
                    sms_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT DEFAULT 'sent'
                )
            ''')

            self.conn.commit()
            return True
        except Exception as e:
            print(f"Table creation error: {str(e)}")
            return False

    def add_call(self, phone_number, call_type, duration=0, notes=None):
        """Add call record to database

        call_type: 'incoming', 'outgoing', 'missed'
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"添加通话记录: {phone_number}, 类型: {call_type}, 持续时间: {duration}秒, 备注: {notes}")
            self.cursor.execute(
                "INSERT INTO call_history (phone_number, call_type, duration, timestamp, notes) VALUES (?, ?, ?, ?, ?)",
                (phone_number, call_type, duration, timestamp, notes)
            )
            self.conn.commit()
            return self.cursor.lastrowid
        except Exception as e:
            print(f"添加通话记录出错: {str(e)}")
            return None

    def add_sms(self, phone_number, message, sms_type, status='sent'):
        """Add SMS record to database

        sms_type: 'incoming', 'outgoing'
        status: 'sent', 'failed', 'received', 'read'
        """
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.cursor.execute(
                "INSERT INTO sms_history (phone_number, message, sms_type, timestamp, status) VALUES (?, ?, ?, ?, ?)",
                (phone_number, message, sms_type, timestamp, status)
            )
            self.conn.commit()
            return self.cursor.lastrowid
        except Exception as e:
            print(f"Add SMS error: {str(e)}")
            return None

    def get_call_history(self, limit=50, offset=0, phone_number=None):
        """Get call history from database"""
        try:
            query = "SELECT * FROM call_history"
            params = []

            if phone_number:
                query += " WHERE phone_number = ?"
                params.append(phone_number)

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            self.cursor.execute(query, params)
            return self.cursor.fetchall()
        except Exception as e:
            print(f"Get call history error: {str(e)}")
            return []

    def get_sms_history(self, limit=50, offset=0, phone_number=None, sms_type=None):
        """Get SMS history from database"""
        try:
            query = "SELECT * FROM sms_history"
            params = []

            conditions = []
            if phone_number:
                conditions.append("phone_number = ?")
                params.append(phone_number)

            if sms_type:
                conditions.append("sms_type = ?")
                params.append(sms_type)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            self.cursor.execute(query, params)
            return self.cursor.fetchall()
        except Exception as e:
            print(f"Get SMS history error: {str(e)}")
            return []

    def update_sms_status(self, sms_id, status):
        """Update SMS status"""
        try:
            self.cursor.execute(
                "UPDATE sms_history SET status = ? WHERE id = ?",
                (status, sms_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Update SMS status error: {str(e)}")
            return False

    def delete_call(self, call_id):
        """Delete call record"""
        try:
            self.cursor.execute("DELETE FROM call_history WHERE id = ?", (call_id,))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Delete call error: {str(e)}")
            return False

    def delete_sms(self, sms_id):
        """Delete SMS record"""
        try:
            self.cursor.execute("DELETE FROM sms_history WHERE id = ?", (sms_id,))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Delete SMS error: {str(e)}")
            return False